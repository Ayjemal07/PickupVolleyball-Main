// Run with: node --test tests/test_merch_gallery.js
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const script = fs.readFileSync(path.join(__dirname, '../app/static/js/merch.js'), 'utf8');

class Element {
    constructor(active = false) {
        this.listeners = {};
        this.attributes = {};
        this.dataset = {};
        this.classes = new Set(active ? ['is-active'] : []);
        this.classList = {toggle: (name, value) => {
            if (value) this.classes.add(name);
            else this.classes.delete(name);
        }};
    }
    addEventListener(name, handler) { this.listeners[name] = handler; }
    setAttribute(name, value) { this.attributes[name] = value; }
    dispatch(name, props = {}) {
        const event = {
            ...props,
            preventDefault() { this.prevented = true; },
            stopPropagation() { this.stopped = true; },
        };
        this.listeners[name]?.(event);
        return event;
    }
}

function gallery(total = 4) {
    const card = new Element();
    const wrap = new Element();
    const prev = new Element();
    const next = new Element();
    const count = new Element();
    const slides = Array.from({length: total}, (_, i) => new Element(i === 0));
    const dots = Array.from({length: total}, (_, i) => {
        const dot = new Element(i === 0);
        dot.dataset.index = String(i);
        return dot;
    });
    wrap.closest = () => card;
    wrap.querySelectorAll = selector => selector === '.merch-image-slide' ? slides : dots;
    wrap.querySelector = selector => ({
        '.is-prev': prev, '.is-next': next, '.merch-gallery-count': count,
    })[selector];
    const context = {
        document: {
            getElementById: () => new Element(),
            querySelectorAll: selector => selector === '.merch-image-wrap' ? [wrap] : [],
            addEventListener: (_, handler) => { context.start = handler; },
        },
        localStorage: {getItem: () => '[]'},
    };
    vm.runInNewContext(script, context);
    context.start();
    return {
        card, wrap, prev, next, count, slides, dots,
        active: () => slides.findIndex(slide => slide.classes.has('is-active')),
    };
}

test('arrows traverse all photos and wrap around in both directions', () => {
    const g = gallery(5);
    for (let i = 1; i <= 5; i++) {
        g.next.dispatch('click');
        assert.equal(g.active(), i % 5);
        assert.equal(g.count.textContent, `Photo ${i % 5 + 1} of 5`);
    }
    g.prev.dispatch('click');
    assert.equal(g.active(), 4);
    assert.equal(g.slides[4].attributes['aria-hidden'], 'false');
    assert.equal(g.slides[0].attributes['aria-hidden'], 'true');
});

test('hovering anywhere on a card previews the second photo and restores the first', () => {
    const g = gallery();
    g.card.dispatch('pointerenter', {pointerType: 'mouse'});
    assert.equal(g.active(), 1);
    g.card.dispatch('pointerleave', {pointerType: 'mouse'});
    assert.equal(g.active(), 0);
    g.card.dispatch('pointerenter', {pointerType: 'touch'});
    assert.equal(g.active(), 0);
});

test('dot selection survives hover and reset restores the primary photo', () => {
    const g = gallery(14);
    g.dots[12].dispatch('click');
    assert.equal(g.active(), 12);
    assert.equal(g.dots[12].attributes['aria-current'], 'true');
    assert.equal(g.dots[0].attributes['aria-current'], 'false');
    g.card.dispatch('pointerenter', {pointerType: 'mouse'});
    g.card.dispatch('pointerleave', {pointerType: 'mouse'});
    assert.equal(g.active(), 12);
    g.wrap.resetGallery();
    assert.equal(g.active(), 0);
});

test('keyboard arrows change photos without navigating the page', () => {
    const g = gallery();
    assert.equal(g.wrap.dispatch('keydown', {key: 'ArrowRight'}).prevented, true);
    assert.equal(g.active(), 1);
    g.wrap.dispatch('keydown', {key: 'ArrowLeft'});
    assert.equal(g.active(), 0);
});

test('horizontal swipes change photos and suppress the resulting product-link click', () => {
    const g = gallery();
    g.wrap.dispatch('pointerdown', {pointerType: 'touch', clientX: 200, clientY: 100});
    g.wrap.dispatch('pointerup', {pointerType: 'touch', clientX: 110, clientY: 105});
    assert.equal(g.active(), 1);
    const click = g.wrap.dispatch('click');
    assert.equal(click.prevented, true);
    assert.equal(click.stopped, true);
    g.wrap.dispatch('pointerdown', {pointerType: 'touch', clientX: 100, clientY: 100});
    g.wrap.dispatch('pointerup', {pointerType: 'touch', clientX: 190, clientY: 105});
    assert.equal(g.active(), 0);
});

test('vertical scrolling and canceled touches leave the selected photo alone', () => {
    const g = gallery();
    g.wrap.dispatch('pointerdown', {pointerType: 'touch', clientX: 200, clientY: 100});
    g.wrap.dispatch('pointerup', {pointerType: 'touch', clientX: 180, clientY: 200});
    assert.equal(g.active(), 0);
    assert.equal(g.wrap.dispatch('click').prevented, undefined);
    g.wrap.dispatch('pointerdown', {pointerType: 'touch', clientX: 200, clientY: 100});
    g.wrap.dispatch('pointercancel');
    g.wrap.dispatch('pointerup', {pointerType: 'touch', clientX: 100, clientY: 100});
    assert.equal(g.active(), 0);
});
