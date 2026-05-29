(function () {
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const revealItems = document.querySelectorAll(".reveal");
  const processBoards = document.querySelectorAll("[data-process-board]");
  const demoContact = document.querySelector("[data-marketing-demo-contact]");

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
  } else {
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
  }

  if (!demoContact) return;

  const phoneNumber = demoContact.querySelector("[data-demo-phone-number]");
  const phoneHint = demoContact.querySelector("[data-demo-phone-hint]");
  const smsLink = demoContact.querySelector("[data-demo-sms-link]");
  const callLink = demoContact.querySelector("[data-demo-call-link]");
  const emailLink = demoContact.querySelector("[data-demo-email-link]");
  const inboxEmail = demoContact.querySelector("[data-demo-inbox-email]");

  function enableLink(link, href) {
    if (!link || !href) return;
    link.href = href;
    link.setAttribute("aria-disabled", "false");
  }

  async function loadMarketingDemoContact() {
    try {
      const response = await fetch("/api/demo/config");
      if (!response.ok) return;
      const config = await response.json();

      if (config.demo_phone_number) {
        phoneNumber.textContent = config.demo_phone_number;
        phoneHint.textContent = "Text it with a sample job, or call it to trigger the missed-call voice preview.";
        enableLink(smsLink, `sms:${config.demo_phone_number}?&body=Hi%2C%20we%20need%20help%20with%20an%20urgent%20plumbing%20issue.`);
        enableLink(callLink, `tel:${config.demo_phone_number}`);
        smsLink.querySelector("strong").textContent = "Text the demo line";
        callLink.querySelector("strong").textContent = "Call the demo line";
        demoContact.classList.add("is-ready");
      } else {
        phoneNumber.textContent = "Instant simulator ready";
        phoneHint.textContent = "The public Twilio demo line is not connected here, but the live simulator is ready.";
      }

      if (config.demo_inbox_email) {
        inboxEmail.textContent = config.demo_inbox_email;
        enableLink(emailLink, `mailto:${config.demo_inbox_email}?subject=Emergency%20plumbing%20demo&body=Hi%2C%20we%20have%20water%20coming%20through%20the%20ceiling%20and%20need%20help%20ASAP.`);
      }
    } catch {
      phoneNumber.textContent = "Instant simulator ready";
      phoneHint.textContent = "The demo contact settings could not be loaded, but the live simulator is ready.";
    }
  }

  loadMarketingDemoContact();
})();
