"""Admin-managed merchandise, variant inventory, and shipment tracking."""

from datetime import datetime
from decimal import Decimal

from alembic import op
import sqlalchemy as sa


revision = '20261027_merch_inventory'
down_revision = '20260927_subscription_checkout'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'merch_products',
        sa.Column('id', sa.String(80), primary_key=True),
        sa.Column('name', sa.String(150), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('price', sa.Numeric(10, 2), nullable=False),
        sa.Column('image', sa.String(255), nullable=False),
        sa.Column('detail_image', sa.String(255), nullable=False),
        sa.Column('detail_position', sa.String(30), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )
    op.create_table(
        'merch_variants',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('product_id', sa.String(80),
                  sa.ForeignKey('merch_products.id'), nullable=False),
        sa.Column('size', sa.String(20), nullable=False),
        sa.Column('color', sa.String(40), nullable=False),
        sa.Column('image', sa.String(255), nullable=True),
        sa.Column('stock_on_hand', sa.Integer(), nullable=False),
        sa.Column('stock_reserved', sa.Integer(), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.UniqueConstraint('product_id', 'size', 'color',
                            name='uq_merch_variant_option'),
    )
    op.create_index('ix_merch_variants_product_id', 'merch_variants',
                    ['product_id'])

    with op.batch_alter_table('merch_orders') as batch_op:
        batch_op.add_column(sa.Column('inventory_reserved', sa.Boolean(),
                                      nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('reservation_expires_at', sa.DateTime(),
                                      nullable=True))
        batch_op.add_column(sa.Column('cancel_token', sa.String(80),
                                      nullable=True))
        batch_op.add_column(sa.Column('tracking_carrier', sa.String(50),
                                      nullable=True))
        batch_op.add_column(sa.Column('tracking_number', sa.String(120),
                                      nullable=True))
        batch_op.add_column(sa.Column('shipped_at', sa.DateTime(),
                                      nullable=True))
        batch_op.add_column(sa.Column('tracking_notified_at', sa.DateTime(),
                                      nullable=True))

    with op.batch_alter_table('merch_order_items') as batch_op:
        batch_op.add_column(sa.Column('variant_id', sa.Integer(),
                                      nullable=True))
        batch_op.add_column(sa.Column('color', sa.String(40),
                                      nullable=False,
                                      server_default='As shown'))
        batch_op.create_foreign_key('fk_merch_item_variant',
                                    'merch_variants', ['variant_id'], ['id'])

    now = datetime.utcnow()
    products = sa.table(
        'merch_products',
        sa.column('id', sa.String),
        sa.column('name', sa.String),
        sa.column('description', sa.Text),
        sa.column('price', sa.Numeric),
        sa.column('image', sa.String),
        sa.column('detail_image', sa.String),
        sa.column('detail_position', sa.String),
        sa.column('active', sa.Boolean),
        sa.column('created_at', sa.DateTime),
        sa.column('updated_at', sa.DateTime),
    )
    seed = [
        ('wildcats-hoodie', 'Wildcats Volleyball Hoodie', '39.99',
         'merch/Hoodie1.jpeg', 'center 43%', ['S', 'M', 'L', 'XL', '2XL']),
        ('volleyball-life-shirt', 'Volleyball Life T-Shirt', '24.99',
         'merch/SCT0395-2_2000x.jpeg', 'center 46%',
         ['S', 'M', 'L', 'XL', '2XL']),
        ('teal-volleyball-shirt', 'Teal Volleyball T-Shirt', '24.99',
         'merch/TealVolleyTshirt.jpeg', 'center 38%',
         ['S', 'M', 'L', 'XL', '2XL']),
        ('pink-volleyball-hat', 'Pink Volleyball Hat', '19.99',
         'merch/VolleyballHatTest.jpeg', 'center 54%', ['One Size']),
    ]
    op.bulk_insert(products, [
        dict(id=slug, name=name, description='', price=Decimal(price),
             image=image, detail_image=image, detail_position=position,
             active=True, created_at=now, updated_at=now)
        for slug, name, price, image, position, _ in seed
    ])

    variants = sa.table(
        'merch_variants',
        sa.column('product_id', sa.String),
        sa.column('size', sa.String),
        sa.column('color', sa.String),
        sa.column('stock_on_hand', sa.Integer),
        sa.column('stock_reserved', sa.Integer),
        sa.column('active', sa.Boolean),
    )
    op.bulk_insert(variants, [
        dict(product_id=slug, size=size, color='As shown',
             stock_on_hand=0, stock_reserved=0, active=True)
        for slug, _, _, _, _, sizes in seed for size in sizes
    ])


def downgrade():
    with op.batch_alter_table('merch_order_items') as batch_op:
        batch_op.drop_constraint('fk_merch_item_variant', type_='foreignkey')
        batch_op.drop_column('color')
        batch_op.drop_column('variant_id')
    with op.batch_alter_table('merch_orders') as batch_op:
        batch_op.drop_column('tracking_notified_at')
        batch_op.drop_column('shipped_at')
        batch_op.drop_column('tracking_number')
        batch_op.drop_column('tracking_carrier')
        batch_op.drop_column('reservation_expires_at')
        batch_op.drop_column('cancel_token')
        batch_op.drop_column('inventory_reserved')
    op.drop_index('ix_merch_variants_product_id', table_name='merch_variants')
    op.drop_table('merch_variants')
    op.drop_table('merch_products')
