// Run with: node --test tests/test_merch_cart.js
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const cartScript = fs.readFileSync(
    path.join(__dirname, '../app/static/js/merch_cart.js'), 'utf8');
const cartKey = 'pickup_main_merch_cart';

class Element {
    constructor() {
        this.children = [];
        this.listeners = {};
        this.dataset = {};
    }
    append(...children) { this.children.push(...children); }
    appendChild(child) { this.append(child); }
    replaceChildren() { this.children = []; }
    setAttribute(name, value) { this[name] = value; }
    addEventListener(name, handler) { this.listeners[name] = handler; }
    click() { if (!this.disabled) this.listeners.click(); }
}

const initialItem = {
    productId: 'test-shirt', variantId: 1, name: 'Test Shirt',
    size: 'M', color: 'Teal', quantity: 1, unitPrice: 20,
    available: 3, image: '/static/images/merch/shirt.png',
};
const settle = () => new Promise(resolve => setImmediate(resolve));

function harness(items = [initialItem]) {
    const elements = new Map();
    const element = id => {
        if (!elements.has(id)) elements.set(id, new Element());
        return elements.get(id);
    };
    const storage = new Map([[cartKey, JSON.stringify(items)]]);
    const page = new Element();
    page.dataset.shippingRate = '7.99';
    const requests = [];
    const context = {
        document: {
            querySelector: () => page,
            getElementById: element,
            createElement: () => new Element(),
            addEventListener: (event, handler) => { context.start = handler; },
        },
        window: {},
        localStorage: {
            getItem: key => storage.get(key),
            setItem: (key, value) => storage.set(key, value),
        },
        fetch: async (url, options) => {
            requests.push({ url, items: JSON.parse(options.body).items });
            return {
                ok: true,
                json: async () => ({
                    items: JSON.parse(options.body).items.map(item => ({
                        ...initialItem, quantity: item.quantity,
                    })),
                }),
            };
        },
    };
    vm.runInNewContext(cartScript, context);
    context.start();
    return {
        element, requests,
        saved: () => JSON.parse(storage.get(cartKey)),
        controls: () => element('merchCartItems').children[0].children[1].children[2].children,
    };
}

test('cart quantities update saved items, server quotes, and totals up to stock', async () => {
    const cart = harness();
    await settle();
    assert.equal(cart.element('merchTotal').textContent, '$27.99');
    assert.equal(cart.element('merchCartItems').children[0].children[1]
        .children[0].children[0].href, '/merch/products/test-shirt');
    cart.controls()[2].click();
    await settle();
    assert.equal(cart.saved()[0].quantity, 2);
    assert.equal(cart.requests.at(-1).items[0].quantity, 2);
    assert.equal(cart.element('merchSubtotal').textContent, '$40.00');
    assert.equal(cart.element('merchTotal').textContent, '$47.99');
    cart.controls()[2].click();
    await settle();
    assert.equal(cart.saved()[0].quantity, 3);
    assert.equal(cart.element('merchTotal').textContent, '$67.99');
    assert.equal(cart.controls()[2].disabled, true);
    cart.controls()[2].click();
    assert.equal(cart.saved()[0].quantity, 3);
    cart.controls()[0].click();
    await settle();
    assert.equal(cart.saved()[0].quantity, 2);
    assert.equal(cart.element('merchTotal').textContent, '$47.99');
});

test('decreasing the last unit empties the cart and hides checkout', async () => {
    const cart = harness();
    await settle();
    cart.controls()[0].click();
    await settle();
    assert.deepEqual(cart.saved(), []);
    assert.equal(cart.element('merchCartItems').children.length, 0);
    assert.equal(cart.element('merchCartEmptyPage').hidden, false);
    assert.equal(cart.element('merchCheckoutLayout').hidden, true);
});

test('remove clears a cart line regardless of quantity', async () => {
    const cart = harness([{ ...initialItem, quantity: 3 }]);
    await settle();
    cart.controls()[3].click();
    assert.deepEqual(cart.saved(), []);
    assert.equal(cart.element('merchCheckoutLayout').hidden, true);
});
