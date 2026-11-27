import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {compileScript, parse} from 'vue/compiler-sfc';
import ts from 'typescript';

const vue = createRequire(import.meta.url)('vue');
const {createRenderer, h, ref} = vue;
export const {nextTick} = vue;
globalThis.Document ??= class Document {};
globalThis.ShadowRoot ??= class ShadowRoot {};

export function compileModule(source, imports = {}) {
    const {outputText} = ts.transpileModule(source, {
        compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}
    });
    const exports = {};
    new Function('require', 'exports', outputText)(name => {
        if (name === 'vue') return vue;
        assert.ok(name in imports, 'missing module ' + name);
        return imports[name];
    }, exports);
    return exports;
}

class Element {
    constructor(tag, text = '') {
        this.tag = tag;
        this.content = text;
        this.children = [];
        this.props = {};
        this.listeners = new Map();
        const classes = new Set();
        this.classList = {
            add: (...names) => names.forEach(name => classes.add(name)),
            remove: (...names) => names.forEach(name => classes.delete(name)),
            contains: name => classes.has(name)
        };
        this.ownerDocument = new Document();
    }

    get options() { return this.children.filter(child => child.tag === 'option'); }
    get selectedIndex() { return this.options.findIndex(option => option.selected); }
    set selectedIndex(index) { this.options.forEach((option, i) => { option.selected = i === index; }); }
    get value() {
        return this.tag === 'select'
            ? this.options[this.selectedIndex]?.value ?? ''
            : this.props.value ?? '';
    }
    set value(value) {
        if (this.tag === 'select') {
            this.selectedIndex = this.options.findIndex(option => option.value === value);
        } else {
            this.props.value = value;
        }
    }
    get type() { return this.props.type; }
    get text() { return this.content + this.children.map(child => child.text).join(''); }
    addEventListener(event, handler) {
        if (!this.listeners.has(event)) this.listeners.set(event, []);
        this.listeners.get(event).push(handler);
    }
    getAnimations() { return []; }
    showModal() { this.open = true; }
    close() { this.open = false; }
    focus() { this.focused = true; this.ownerDocument.activeElement = this; }
    matches() { return Boolean(this.autofilled); }
    getRootNode() { return this.ownerDocument; }
    reportValidity() { return true; }
}

const renderer = createRenderer({
    createElement: tag => new Element(tag),
    createText: text => new Element('#text', text),
    createComment: text => new Element('#comment', text),
    setText: (node, text) => { node.content = text; },
    setElementText: (node, text) => { node.content = text; node.children.length = 0; },
    parentNode: node => node.parent,
    nextSibling: node => node.parent.children[node.parent.children.indexOf(node) + 1],
    patchProp: (node, key, previous, value) => { node.props[key] = value; },
    insert(node, parent, anchor) {
        if (node.parent) node.parent.children.splice(node.parent.children.indexOf(node), 1);
        node.parent = parent;
        parent.children.splice(anchor ? parent.children.indexOf(anchor) : parent.children.length, 0, node);
    },
    remove(node) {
        node.parent.children.splice(node.parent.children.indexOf(node), 1);
        node.parent = undefined;
    }
});

export function mountComponent(file, {imports = {}, props = {}} = {}) {
    const source = readFileSync(file, 'utf8');
    const {descriptor, errors} = parse(source, {filename: file.pathname});
    assert.deepEqual(errors, []);
    const script = compileScript(descriptor, {
        id: 'component-test', inlineTemplate: true,
        templateOptions: {compilerOptions: {hoistStatic: false}}
    }).content;
    const component = compileModule(script, imports).default;
    const root = new Element('root');
    const componentRef = ref();
    const wrapper = {
        inheritAttrs: false,
        setup(_, {attrs}) {
            return () => h(component, {...attrs, ref: componentRef});
        }
    };
    const attributes = {...props};
    function update(next = {}) {
        Object.assign(attributes, next);
        renderer.render(h(wrapper, {...attributes}), root);
    }
    update();

    function find(node, id) {
        if (node.props.id === id) return node;
        for (const child of node.children) {
            const result = find(child, id);
            if (result) return result;
        }
    }
    const element = id => {
        const node = find(root, id);
        assert.ok(node, 'missing element ' + id);
        return node;
    };
    function act(id, event = 'click', properties = {}) {
        const node = element(id);
        const args = {target: node, currentTarget: node, preventDefault() {}, stopPropagation() {}, ...properties};
        for (const handler of node.listeners.get(event) ?? []) handler(args);
        return node.props['on' + event[0].toUpperCase() + event.slice(1)]?.(args);
    }
    function select(id, value) {
        element(id).value = value;
        return act(id, 'change');
    }
    function input(id, value, properties = {}) {
        element(id).value = value;
        return act(id, 'input', properties);
    }
    return {element, act, select, input, update, session: componentRef.value, unmount: () => renderer.render(null, root)};
}
