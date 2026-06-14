declare module "katex/contrib/auto-render" {
  const renderMathInElement: (el: HTMLElement, opts?: Record<string, unknown>) => void;
  export default renderMathInElement;
}
