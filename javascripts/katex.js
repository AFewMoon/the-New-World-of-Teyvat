var renderKaTeX = function () {
  var body = document.body;
  if (typeof renderMathInElement !== "undefined") {
    renderMathInElement(body, {
      delimiters: [
        { left: "$$", right: "$$", display: true },
        { left: "$", right: "$", display: false },
        { left: "\\(", right: "\\)", display: false },
        { left: "\\[", right: "\\]", display: true },
      ],
    });
  }
};

if (typeof document$ !== "undefined") {
  document$.subscribe(renderKaTeX);
}
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", renderKaTeX);
} else {
  renderKaTeX();
}
