// DOMPurify needs a real browser window. Tests feed trusted markup, so sanitising is a pass-through here.
export default { sanitize: (html) => String(html) };
