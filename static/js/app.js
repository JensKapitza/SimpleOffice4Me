(() => {
  "use strict";

  function initProjectQuickWins() {
    const taskSection = document.getElementById("aufgaben");
    if (!taskSection) return;

    const taskCards = Array.from(taskSection.querySelectorAll('article[id^="task-"]'));
    const main = taskSection.closest("main");
    if (!main) return;

    const statusLabels = {
      open: "Offen",
      in_progress: "In Arbeit",
      waiting: "Wartet",
      completed: "Erledigt",
      cancelled: "Abgebrochen"
    };

    const taskData = taskCards.map((card) => {
      const status = card.querySelector('.card-header .badge')?.textContent.trim() || "open";
      const title = card.querySelector('.card-header strong')?.textContent.trim() || "";
      const form = card.querySelector('form[action*="/tasks/"]');
      const resources = form?.querySelector('[name="resources"]')?.value || "";
      const timeText = Array.from(card.querySelectorAll('.card-header .badge'))
        .map((badge) => badge.textContent.trim())
        .find((text) => text.includes("h gebucht")) || "0:00 h gebucht";
      const match = timeText.match(/(\d+):(\d{2})\s*h/);
      const minutes = match ? Number(match[1]) * 60 + Number(match[2]) : 0;
      return { card, status, title, resources, minutes };
    });

    // 1) Projektcockpit: Fortschritt und Kennzahlen aus vorhandenen Aufgaben.
    const relevantTasks = taskData.filter((task) => task.status !== "cancelled");
    const completedTasks = relevantTasks.filter((task) => task.status === "completed").length;
    const activeTasks = relevantTasks.filter((task) => task.status === "in_progress").length;
    const waitingTasks = relevantTasks.filter((task) => task.status === "waiting").length;
    const totalMinutes = taskData.reduce((sum, task) => sum + task.minutes, 0);
    const progress = relevantTasks.length ? Math.round((completedTasks / relevantTasks.length) * 100) : 0;

    const cockpit = document.createElement("section");
    cockpit.className = "card mb-4";
    cockpit.id = "project-cockpit";
    cockpit.innerHTML = `
      <div class="card-header d-flex justify-content-between align-items-center gap-2">
        <strong>Projektcockpit</strong>
        <span class="badge text-bg-light border">${progress} % erledigt</span>
      </div>
      <div class="card-body">
        <div class="progress mb-3" role="progressbar" aria-label="Projektfortschritt" aria-valuenow="${progress}" aria-valuemin="0" aria-valuemax="100">
          <div class="progress-bar" style="width: ${progress}%">${progress} %</div>
        </div>
        <div class="row g-2 text-center">
          <div class="col-6 col-md-3"><div class="border rounded p-2"><div class="fs-5 fw-semibold">${relevantTasks.length}</div><div class="small text-secondary">Aufgaben</div></div></div>
          <div class="col-6 col-md-3"><div class="border rounded p-2"><div class="fs-5 fw-semibold">${activeTasks}</div><div class="small text-secondary">In Arbeit</div></div></div>
          <div class="col-6 col-md-3"><div class="border rounded p-2"><div class="fs-5 fw-semibold">${waitingTasks}</div><div class="small text-secondary">Wartend</div></div></div>
          <div class="col-6 col-md-3"><div class="border rounded p-2"><div class="fs-5 fw-semibold">${Math.floor(totalMinutes / 60)}:${String(totalMinutes % 60).padStart(2, "0")}</div><div class="small text-secondary">Gebuchte Stunden</div></div></div>
        </div>
      </div>`;

    const firstProjectCard = main.querySelector("section.card");
    if (firstProjectCard) firstProjectCard.before(cockpit);

    // 2) Terminampel: Projektende aus dem bereits vorhandenen Feld auswerten.
    const projectEndInput = main.querySelector('input[name="planned_end"]');
    const projectStatus = main.querySelector('.d-flex.justify-content-between .badge')?.textContent.trim() || "";
    if (projectEndInput?.value && !["completed", "cancelled"].includes(projectStatus)) {
      const end = new Date(`${projectEndInput.value}T00:00:00`);
      const today = new Date();
      today.setHours(0, 0, 0, 0);
      const days = Math.ceil((end - today) / 86400000);
      const alert = document.createElement("div");
      if (days < 0) {
        alert.className = "alert alert-danger mb-4";
        alert.textContent = `Projekttermin seit ${Math.abs(days)} Tag(en) überschritten (${projectEndInput.value}).`;
      } else if (days <= 7) {
        alert.className = "alert alert-warning mb-4";
        alert.textContent = `Projekttermin in ${days} Tag(en) (${projectEndInput.value}).`;
      } else {
        alert.className = "alert alert-light border mb-4";
        alert.textContent = `Geplantes Projektende: ${projectEndInput.value} · noch ${days} Tage.`;
      }
      cockpit.after(alert);
    }

    // 3) Aufgabenfilter: Volltext + Status ohne Serveranfrage.
    if (taskCards.length) {
      const controls = document.createElement("div");
      controls.className = "card my-3";
      controls.innerHTML = `
        <div class="card-body py-2">
          <div class="row g-2 align-items-center">
            <div class="col-md-6"><input type="search" class="form-control form-control-sm" id="project-task-filter" placeholder="Aufgaben, Beschreibung oder Ressource filtern …"></div>
            <div class="col-md-3"><select class="form-select form-select-sm" id="project-task-status"><option value="">Alle Status</option>${Object.entries(statusLabels).map(([value, label]) => `<option value="${value}">${label}</option>`).join("")}</select></div>
            <div class="col-md-3"><div class="btn-group btn-group-sm w-100"><button type="button" class="btn btn-outline-secondary" id="project-expand-all">Alle öffnen</button><button type="button" class="btn btn-outline-secondary" id="project-collapse-all">Alle zuklappen</button></div></div>
          </div>
          <div class="small text-secondary mt-2" id="project-task-filter-count"></div>
        </div>`;

      const newTask = document.getElementById("new-task");
      if (newTask) newTask.after(controls);
      else taskSection.prepend(controls);

      const filterInput = controls.querySelector("#project-task-filter");
      const statusSelect = controls.querySelector("#project-task-status");
      const count = controls.querySelector("#project-task-filter-count");

      const applyFilter = () => {
        const query = filterInput.value.trim().toLowerCase();
        const status = statusSelect.value;
        let visible = 0;
        taskData.forEach((task) => {
          const searchable = `${task.title} ${task.resources} ${task.card.textContent}`.toLowerCase();
          const show = (!query || searchable.includes(query)) && (!status || task.status === status);
          task.card.classList.toggle("d-none", !show);
          if (show) visible += 1;
        });
        count.textContent = `${visible} von ${taskData.length} Aufgabe(n) sichtbar`;
      };
      filterInput.addEventListener("input", applyFilter);
      statusSelect.addEventListener("change", applyFilter);
      applyFilter();

      // 4) Schnellansicht: Aufgabeninhalte gesammelt ein- oder ausblenden.
      controls.querySelector("#project-expand-all").addEventListener("click", () => {
        taskData.forEach((task) => task.card.querySelector(".card-body")?.classList.remove("d-none"));
      });
      controls.querySelector("#project-collapse-all").addEventListener("click", () => {
        taskData.forEach((task) => task.card.querySelector(".card-body")?.classList.add("d-none"));
      });
    }

    // 5) Projektzusammenfassung: kompakte Statusübersicht in die Zwischenablage kopieren.
    const title = main.querySelector("h1")?.textContent.trim() || "Projekt";
    const summaryButton = document.createElement("button");
    summaryButton.type = "button";
    summaryButton.className = "btn btn-sm btn-outline-secondary ms-2";
    summaryButton.innerHTML = '<i class="fas fa-copy me-1" aria-hidden="true"></i>Zusammenfassung kopieren';
    cockpit.querySelector(".card-header")?.append(summaryButton);

    summaryButton.addEventListener("click", async () => {
      const lines = [
        title,
        `Fortschritt: ${progress} % (${completedTasks}/${relevantTasks.length} Aufgaben erledigt)`,
        `In Arbeit: ${activeTasks} · Wartend: ${waitingTasks}`,
        `Gebuchte Zeit: ${Math.floor(totalMinutes / 60)}:${String(totalMinutes % 60).padStart(2, "0")} h`,
        projectEndInput?.value ? `Geplantes Ende: ${projectEndInput.value}` : "Geplantes Ende: nicht gesetzt",
        "",
        ...taskData.map((task) => `- [${statusLabels[task.status] || task.status}] ${task.title}`)
      ];
      const text = lines.join("\n");
      try {
        if (navigator.clipboard?.writeText) {
          await navigator.clipboard.writeText(text);
        } else {
          const area = document.createElement("textarea");
          area.value = text;
          area.style.position = "fixed";
          area.style.opacity = "0";
          document.body.append(area);
          area.select();
          document.execCommand("copy");
          area.remove();
        }
        const original = summaryButton.innerHTML;
        summaryButton.textContent = "Kopiert";
        setTimeout(() => { summaryButton.innerHTML = original; }, 1500);
      } catch (error) {
        console.warn("Projektzusammenfassung konnte nicht kopiert werden", error);
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initProjectQuickWins);
  } else {
    initProjectQuickWins();
  }
})();
