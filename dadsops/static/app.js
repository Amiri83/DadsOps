// Show feedback while a tool starts: the request blocks until its port answers.
document.addEventListener("submit", (event) => {
  const form = event.target.closest("form.open");
  if (!form) return;
  form.closest(".card").classList.add("is-starting");
});

// Same for the top bar tool switcher: opening a tool page may start the tool first.
document.addEventListener("click", (event) => {
  const link = event.target.closest("a.tool-link");
  if (link && !link.hasAttribute("aria-current")) link.classList.add("is-starting");
});
