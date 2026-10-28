(function(){
  const absorbe = () => new Proxy(function(){}, {
    get: (t, k) => k === Symbol.toPrimitive ? (() => '') : (k in t ? t[k] : (t[k] = absorbe())),
    set: (t, k, v) => { t[k] = v; return true; },
    apply: () => absorbe(), construct: () => absorbe() });
  window.Chart = absorbe();
})();
