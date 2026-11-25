// Run with: node --test tests/test_calendar_navigation.js
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const script = fs.readFileSync(path.join(__dirname, '../app/static/js/events.js'), 'utf8');

function card(id) {
    const classes = new Set();
    return {
        dataset: {eventId: String(id)},
        classes,
        classList: {add: name => classes.add(name), remove: name => classes.delete(name)},
        setAttribute(name, value) { this[name] = value; },
        focus(options) { this.focusOptions = options; },
        scrollIntoView(options) { this.scrollOptions = options; },
    };
}

function navigation({calendarCards = [], pastCards = [], reducedMotion = false} = {}) {
    const timers = new Map();
    let nextTimer = 0;
    const state = {pastOpened: false, destination: null};
    const panes = {
        'calendar-view': {querySelectorAll: () => calendarCards},
        past: {querySelectorAll: () => pastCards},
    };
    const context = {
        document: {
            getElementById: id => panes[id] || null,
            querySelector: selector => selector.includes('href="#past"')
                ? {click: () => { state.pastOpened = true; }} : null,
            querySelectorAll: selector => selector === '.event-card.is-calendar-selected'
                ? [...calendarCards, ...pastCards].filter(c => c.classes.has('is-calendar-selected')) : [],
            addEventListener() {},
        },
        window: {
            location: {assign: destination => { state.destination = destination; }},
            matchMedia: () => ({matches: reducedMotion}),
            clearTimeout: id => timers.delete(id),
            setTimeout: callback => { timers.set(++nextTimer, callback); return nextTimer; },
        },
    };
    vm.runInNewContext(script, context);
    return {
        state, timers,
        click(id) {
            const event = {
                preventDefault() { this.prevented = true; },
                stopPropagation() { this.stopped = true; },
            };
            context.scrollToCalendarEvent({event: {id}, jsEvent: event});
            return event;
        },
    };
}

test('calendar clicks focus and scroll to the exact event card below the calendar', () => {
    const first = card(10);
    const selected = card(11);
    const nav = navigation({calendarCards: [first, selected]});
    const click = nav.click('11');
    assert.equal(first.scrollOptions, undefined);
    assert.equal(selected.scrollOptions.behavior, 'smooth');
    assert.equal(selected.scrollOptions.block, 'start');
    assert.equal(selected.focusOptions.preventScroll, true);
    assert.equal(selected.tabindex, '-1');
    assert.equal(selected.classes.has('is-calendar-selected'), true);
    assert.equal(nav.state.pastOpened, false);
    assert.equal(nav.state.destination, null);
    assert.equal(click.prevented, true);
    assert.equal(click.stopped, true);
});

test('past events open the past tab before focusing their matching card', () => {
    const selected = card(21);
    const nav = navigation({pastCards: [selected]});
    nav.click(21);
    assert.equal(nav.state.pastOpened, true);
    assert.equal(selected.classes.has('is-calendar-selected'), true);
    assert.equal(selected.scrollOptions.block, 'start');
});

test('reduced motion skips smooth scrolling', () => {
    const selected = card(10);
    navigation({calendarCards: [selected], reducedMotion: true}).click(10);
    assert.equal(selected.scrollOptions.behavior, 'instant');
});

test('repeated clicks replace the highlight and cancel its previous timer', () => {
    const first = card(10);
    const second = card(11);
    const nav = navigation({calendarCards: [first, second]});
    nav.click(10);
    nav.click(11);
    assert.equal(first.classes.has('is-calendar-selected'), false);
    assert.equal(second.classes.has('is-calendar-selected'), true);
    assert.equal(nav.timers.size, 1);
    [...nav.timers.values()][0]();
    assert.equal(second.classes.has('is-calendar-selected'), false);
});

test('a missing card opens the event detail page instead of selecting another event', () => {
    const nav = navigation({calendarCards: [card(10)]});
    nav.click('11');
    assert.equal(nav.state.destination, '/events/11');
    assert.equal(nav.timers.size, 0);
});
