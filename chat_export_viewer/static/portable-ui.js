/* Loaded only by the portable edition. */
(() => {
  const quit = document.getElementById("quit-archive");
  quit.addEventListener("click", async () => {
    if (window.ArchiveHasUnsavedChanges?.()) {
      document.getElementById("import-status").textContent = "Please save or cancel your tag edits before quitting.";
      return;
    }
    quit.disabled = true;
    try {
      const session = await fetch("/__archive_session");
      if (!session.ok) throw new Error("Reopen The Archive.exe to reconnect to the app.");
      const { quit_token: token } = await session.json();
      const response = await fetch("/__archive_quit", { method: "POST", headers: { "X-Archive-Token": token } });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "The app could not close yet. Please try again.");
      const panel = document.createElement("main");
      panel.className = "portable-closed";
      const title = document.createElement("h1");
      title.textContent = "The Archive is closed.";
      const description = document.createElement("p");
      description.textContent = "Your chats are saved. You can close this tab. Double-click The Archive.exe whenever you want to return.";
      panel.append(title, description);
      document.body.replaceChildren(panel);
    } catch (error) {
      document.getElementById("import-status").textContent = error.message;
      quit.disabled = false;
    }
  });
})();
