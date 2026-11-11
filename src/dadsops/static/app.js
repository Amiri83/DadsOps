// Show feedback while a tool starts: the request blocks until its port answers.
document.addEventListener("click", (event) => {
  const link = event.target.closest(".card-link");
  if (!link) return;
  link.closest(".card").classList.add("is-starting");
});
