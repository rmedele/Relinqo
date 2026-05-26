(function () {
  const contactUser = ["reese", "medele"].join("");
  const contactDomain = ["gmail", "com"].join(".");
  const contactSubject = encodeURIComponent("relinqo question");
  const contactBody = encodeURIComponent("Hi, I have a question about relinqo.");
  const contactHref = `mailto:${contactUser}@${contactDomain}?subject=${contactSubject}&body=${contactBody}`;
  document.querySelectorAll("[data-email-link]").forEach((link) => {
    link.setAttribute("href", contactHref);
  });

  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const revealItems = document.querySelectorAll(".reveal");
  const processBoards = document.querySelectorAll("[data-process-board]");

  processBoards.forEach((board) => {
    board.dataset.processStep = "0";
  });

  if (!reducedMotion && processBoards.length) {
    let activeStep = 0;
    window.setInterval(() => {
      activeStep = (activeStep + 1) % 4;
      processBoards.forEach((board) => {
        board.dataset.processStep = String(activeStep);
      });
    }, 1800);
  }

  if (!("IntersectionObserver" in window) || reducedMotion) {
    revealItems.forEach((item) => item.classList.add("is-visible"));
    return;
  }

  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      entry.target.classList.add("is-visible");
      observer.unobserve(entry.target);
    });
  }, { threshold: 0.14, rootMargin: "0px 0px -8% 0px" });

  revealItems.forEach((item, index) => {
    item.style.transitionDelay = `${Math.min(index % 3, 2) * 55}ms`;
    observer.observe(item);
  });
})();
