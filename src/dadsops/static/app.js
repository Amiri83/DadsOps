// Show feedback while a tool starts: the request blocks until its port answers.
document.addEventListener("submit", (event) => {
  const form = event.target.closest("form.open");
  if (!form) return;
  form.closest(".card").classList.add("is-starting");
});
