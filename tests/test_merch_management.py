"""Merch listing, inventory, payment, and tracking integration checks."""

from datetime import datetime, timedelta
from decimal import Decimal
from io import BytesIO
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
import requests

os.environ['DATABASE_URI'] = 'sqlite:///:memory:'

import werkzeug
if not hasattr(werkzeug, '__version__'):
    werkzeug.__version__ = '3.1.2'

from app import create_app
from flask import g
from app.models import db, MerchOrder, MerchProduct, MerchProductImage, MerchVariant, User
from app import views


class FakePayPalResponse:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class MerchManagementTests(unittest.TestCase):
    def setUp(self):
        self.uploads = tempfile.TemporaryDirectory(
            dir=Path(__file__).resolve().parents[1])
        self.app = create_app()
        self.app.config.update(TESTING=True, SECRET_KEY='merch-test',
                               MERCH_UPLOAD_FOLDER=self.uploads.name)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        admin = User(first_name='Admin', last_name='User',
                     email='admin@example.com', role='admin')
        admin.id = 'admin'
        shopper = User(first_name='Shopper', last_name='User',
                       email='shopper@example.com', role='user')
        shopper.id = 'shopper'
        db.session.add_all([admin, shopper])
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()
        self.uploads.cleanup()

    def login(self, user_id):
        with self.client.session_transaction() as session:
            session['_user_id'] = user_id
            session['_fresh'] = True
        g.pop('_login_user', None)

    def admin_token(self):
        response = self.client.get('/merch/admin/products/new')
        self.assertEqual(response.status_code, 200)
        return re.search(rb'name="admin_token" value="([^"]+)"',
                         response.data).group(1).decode()

    def add_product(self, stock=2):
        product = MerchProduct(
            id='test-shirt', name='Test Shirt', description='',
            price=Decimal('20.00'), image='merch/TealVolleyTshirt.jpeg',
            detail_image='merch/TealVolleyTshirt.jpeg',
            detail_position='center', active=True,
        )
        product.variants.append(MerchVariant(
            size='M', color='Teal', stock_on_hand=stock, active=True))
        db.session.add(product)
        db.session.commit()
        return product.variants[0].id

    def order_payload(self, variant_id, quantity=1):
        return {
            'expected_subtotal': f'{20 * quantity:.2f}',
            'customer': {
                'name': 'Test Shopper', 'email': 'buyer@example.com',
                'fulfillment_method': 'shipping',
            },
            'shipping_address': {
                'name': 'Test Shopper', 'address_line_1': '123 Main St',
                'city': 'Portland', 'state': 'OR', 'postal_code': '97201',
                'country': 'US',
            },
            'items': [{'product_id': 'test-shirt',
                       'variant_id': variant_id, 'quantity': quantity}],
        }

    def test_admin_listing_and_permissions(self):
        self.assertNotIn(b'Merch admin', self.client.get('/merch').data)
        self.login('shopper')
        self.assertEqual(self.client.get('/merch/admin/products/new').status_code,
                         403)
        self.assertEqual(self.client.get('/merch/admin/orders').status_code,
                         403)
        self.login('admin')
        token = self.admin_token()
        response = self.client.post(
            '/merch/admin/products/new',
            data={
                'admin_token': token, 'name': 'New Hat', 'price': '24.99',
                'description': 'Soft cap', 'active': 'on',
                'new_color[]': 'Pink', 'new_size[]': 'One Size',
                'new_stock[]': '3',
                'new_image[]': (BytesIO(b'\x89PNG\r\n\x1a\n' + b'photo'),
                                'pink.png'),
                'image': (BytesIO(b'\xff\xd8\xff' + b'photo'), 'hat.jpg'),
            },
            content_type='multipart/form-data',
        )
        self.assertEqual(response.status_code, 302)
        product = db.session.get(MerchProduct, 'new-hat')
        self.assertEqual(product.variants[0].stock_on_hand, 3)
        self.assertTrue(product.variants[0].image.endswith('.png'))
        variant_id = product.variants[0].id
        edited = self.client.post(
            '/merch/admin/products/new-hat/edit',
            data={'admin_token': token, 'name': 'New Hat', 'price': '26.99',
                  'description': 'Soft cap', 'active': 'on',
                  f'stock_{variant_id}': '5',
                  f'active_{variant_id}': 'on'},
        )
        self.assertEqual(edited.status_code, 302)
        db.session.refresh(product)
        self.assertEqual(product.variants[0].stock_on_hand, 5)
        self.assertEqual(product.price, Decimal('26.99'))
        self.assertIn(b'Pink', self.client.get('/merch').data)
        self.assertIn(b'Orders &amp; tracking', self.client.get('/merch').data)
        self.assertEqual(self.client.post(
            '/merch/admin/products/new', data={'admin_token': 'wrong'}
        ).status_code, 403)

    def test_admin_gallery_upload_reorder_and_remove(self):
        variant_id = self.add_product()
        self.login('admin')
        token = self.admin_token()
        response = self.client.post(
            '/merch/admin/products/test-shirt/edit',
            data={
                'admin_token': token, 'name': 'Test Shirt', 'price': '20.00',
                'active': 'on', f'stock_{variant_id}': '2',
                f'active_{variant_id}': 'on',
                'gallery_images': [
                    (BytesIO(b'\x89PNG\r\n\x1a\nphoto-one'), 'front.png'),
                    (BytesIO(b'\xff\xd8\xffphoto-two'), 'back.jpg'),
                ],
            },
            content_type='multipart/form-data',
        )
        self.assertEqual(response.status_code, 302)
        product = db.session.get(MerchProduct, 'test-shirt')
        self.assertEqual(len(product.gallery_images), 2)
        first, second = product.gallery_images
        listing = self.client.get('/merch').data
        self.assertIn(first.image.encode(), listing)
        self.assertIn(second.image.encode(), listing)
        self.assertIn(b'Show photo 4 of 4', listing)
        response = self.client.post(
            '/merch/admin/products/test-shirt/edit',
            data={
                'admin_token': token, 'name': 'Test Shirt', 'price': '20.00',
                'active': 'on', f'stock_{variant_id}': '2',
                f'active_{variant_id}': 'on',
                f'gallery_order_{first.id}': '2',
                f'gallery_order_{second.id}': '1',
            },
        )
        self.assertEqual(response.status_code, 302)
        db.session.expire_all()
        self.assertEqual([image.id for image in product.gallery_images],
                         [second.id, first.id])
        response = self.client.post(
            '/merch/admin/products/test-shirt/edit',
            data={
                'admin_token': token, 'name': 'Test Shirt', 'price': '20.00',
                'active': 'on', f'stock_{variant_id}': '2',
                f'active_{variant_id}': 'on',
                f'gallery_order_{first.id}': '2',
                f'gallery_order_{second.id}': '1',
                f'gallery_remove_{first.id}': 'on',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(db.session.get(MerchProductImage, first.id))
        self.assertIn(b'Show photo 3 of 3', self.client.get('/merch').data)

    def test_stock_reservation_capture_and_tracking(self):
        variant_id = self.add_product(stock=1)
        payload = self.order_payload(variant_id)
        quote = self.client.post('/api/merch/quote', json=payload)
        self.assertEqual(quote.status_code, 200)
        self.assertEqual(quote.json['total'], '27.99')
        stale = dict(payload, expected_subtotal='19.99')
        self.assertEqual(self.client.post('/api/merch/orders',
                                          json=stale).status_code, 409)

        def paypal_post(url, **kwargs):
            if url.endswith('/capture'):
                return FakePayPalResponse({
                    'status': 'COMPLETED',
                    'purchase_units': [{'payments': {'captures': [{
                        'id': 'CAPTURE-1',
                        'amount': {'currency_code': 'USD', 'value': '27.99'},
                    }]}}],
                })
            self.assertEqual(
                kwargs['json']['payment_source']['paypal']
                ['experience_context']['shipping_preference'],
                'SET_PROVIDED_ADDRESS')
            self.assertEqual(kwargs['json']['purchase_units'][0]
                             ['shipping']['address']['postal_code'], '97201')
            return FakePayPalResponse({'id': 'PAYPAL-ORDER-1'})

        with patch.object(views, 'get_access_token', return_value='token'), \
             patch.object(views.requests, 'post', side_effect=paypal_post):
            created = self.client.post('/api/merch/orders', json=payload)
            self.assertEqual(created.status_code, 200)
            self.assertEqual(created.json['id'], 'PAYPAL-ORDER-1')
            variant = db.session.get(MerchVariant, variant_id)
            db.session.refresh(variant)
            self.assertEqual((variant.stock_on_hand, variant.stock_reserved),
                             (1, 1))
            self.assertEqual(self.client.post('/api/merch/orders',
                                              json=payload).status_code, 400)
            captured = self.client.post(
                '/api/merch/orders/PAYPAL-ORDER-1/capture')
            self.assertEqual(captured.status_code, 200)
            self.assertEqual(self.client.post(
                '/api/merch/orders/PAYPAL-ORDER-1/capture').status_code, 200)

        db.session.refresh(variant)
        self.assertEqual((variant.stock_on_hand, variant.stock_reserved), (0, 0))
        order = MerchOrder.query.one()
        self.assertEqual(order.items[0].color, 'Teal')
        self.login('admin')
        token = self.admin_token()
        response = self.client.post(
            f'/merch/admin/orders/{order.id}/tracking',
            data={'admin_token': token, 'carrier': 'USPS',
                  'tracking_number': '9400123456789012345678'},
        )
        self.assertEqual(response.status_code, 302)
        db.session.refresh(order)
        self.assertIsNotNone(order.shipped_at)
        self.assertIn(b'9400123456789012345678',
                      self.client.get('/merch/admin/orders').data)
        with patch.object(views.mail, 'send') as send:
            self.client.post(
                f'/merch/admin/orders/{order.id}/tracking',
                data={'admin_token': token, 'carrier': 'USPS',
                      'tracking_number': '9400123456789012345678',
                      'notify_customer': 'on'},
            )
            send.assert_called_once()
        db.session.refresh(order)
        self.assertIsNotNone(order.tracking_notified_at)

    def test_expired_reservation_returns_stock(self):
        variant_id = self.add_product(stock=1)
        variant = db.session.get(MerchVariant, variant_id)
        variant.stock_reserved = 1
        order = MerchOrder(
            order_number='MERCH-EXPIRED', customer_name='Buyer',
            customer_email='buyer@example.com', fulfillment_method='shipping',
            subtotal=Decimal('20.00'), shipping_amount=Decimal('7.99'),
            total=Decimal('27.99'), currency='USD', status='pending',
            inventory_reserved=True,
            reservation_expires_at=datetime.utcnow() - timedelta(minutes=1),
        )
        order.items.append(views.MerchOrderItem(
            product_id='test-shirt', variant_id=variant_id,
            product_name='Test Shirt', size='M', color='Teal', quantity=1,
            unit_price=Decimal('20.00'),
        ))
        db.session.add(order)
        db.session.commit()
        self.client.get('/merch')
        db.session.refresh(variant)
        db.session.refresh(order)
        self.assertEqual(variant.stock_reserved, 0)
        self.assertEqual(order.status, 'expired')

    def test_cancelled_checkout_releases_stock(self):
        variant_id = self.add_product(stock=1)
        with patch.object(views, 'get_access_token', return_value='token'), \
             patch.object(views.requests, 'post', return_value=FakePayPalResponse(
                 {'id': 'PAYPAL-CANCEL-1'})):
            created = self.client.post(
                '/api/merch/orders', json=self.order_payload(variant_id))
        self.assertEqual(created.status_code, 200)
        self.assertEqual(self.client.post(
            '/api/merch/orders/PAYPAL-CANCEL-1/cancel',
            json={'cancel_token': 'wrong'}).status_code, 404)
        self.assertEqual(self.client.post(
            '/api/merch/orders/PAYPAL-CANCEL-1/cancel',
            json={'cancel_token': created.json['cancel_token']}).status_code,
            200)
        variant = db.session.get(MerchVariant, variant_id)
        db.session.refresh(variant)
        self.assertEqual(variant.available, 1)
        self.assertEqual(MerchOrder.query.one().status, 'cancelled')
        self.assertEqual(self.client.post(
            '/api/merch/orders/PAYPAL-CANCEL-1/capture').status_code, 409)

    def test_interrupted_capture_keeps_stock_until_admin_retry(self):
        variant_id = self.add_product(stock=1)
        with patch.object(views, 'get_access_token', return_value='token'), \
             patch.object(views.requests, 'post', side_effect=[
                 FakePayPalResponse({'id': 'PAYPAL-RETRY-1'}),
                 requests.Timeout('capture timed out'),
             ]):
            created = self.client.post(
                '/api/merch/orders', json=self.order_payload(variant_id))
            capture = self.client.post(
                '/api/merch/orders/PAYPAL-RETRY-1/capture')
        self.assertEqual(created.status_code, 200)
        self.assertEqual(capture.status_code, 502)
        order = MerchOrder.query.one()
        self.assertEqual(order.status, 'capturing')
        self.assertEqual(self.client.post(
            '/api/merch/orders/PAYPAL-RETRY-1/cancel',
            json={'cancel_token': created.json['cancel_token']}).status_code,
            409)
        variant = db.session.get(MerchVariant, variant_id)
        db.session.refresh(variant)
        self.assertEqual((variant.stock_on_hand, variant.stock_reserved),
                         (1, 1))

        self.login('admin')
        token = self.admin_token()
        with patch.object(views, 'get_access_token', return_value='token'), \
             patch.object(views.requests, 'post', return_value=FakePayPalResponse({
                 'status': 'COMPLETED',
                 'purchase_units': [{'payments': {'captures': [{
                     'id': 'CAPTURE-RETRY-1',
                     'amount': {'currency_code': 'USD', 'value': '27.99'},
                 }]}}],
             })):
            result = self.client.post(
                f'/merch/admin/orders/{order.id}/retry-capture',
                data={'admin_token': token})
        self.assertEqual(result.status_code, 302)
        db.session.refresh(order)
        db.session.refresh(variant)
        self.assertEqual(order.status, 'completed')
        self.assertEqual((variant.stock_on_hand, variant.stock_reserved),
                         (0, 0))


if __name__ == '__main__':
    unittest.main()
