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

    const relevantTasks = taskData.filter((task) => task.status !== "cancelled");
    const completedTasks = relevantTasks.filter((task) => task.status === "completed").length;
    const activeTasks = relevantTasks.filter((task) => task.status === "in_progress").length;
    const waitingTasks = relevantTasks.filter((task) => task.status === "waiting").length;
    const totalMinutes = taskData.reduce((sum, task) => sum + task.minutes, 0);
    const progress = relevantTasks.length ? Math.round((completedTasks / relevantTasks.length) * 100) : 0;

    const cockpit = document.createElement("section");
    cockpit.className = "card mb-4";
    cockpit.id = "project-cockpit";
    const header = document.createElement("div");
    header.className = "card-header d-flex justify-content-between align-items-center gap-2";
    const heading = document.createElement("strong");
    heading.textContent = "Projektcockpit";
    const progressBadge = document.createElement("span");
    progressBadge.className = "badge text-bg-light border";
    progressBadge.textContent = `${progress} % erledigt`;
    header.append(heading, progressBadge);

    const body = document.createElement("div");
    body.className = "card-body";
    const progressOuter = document.createElement("div");
    progressOuter.className = "progress mb-3";
    progressOuter.setAttribute("role", "progressbar");
    progressOuter.setAttribute("aria-label", "Projektfortschritt");
    progressOuter.setAttribute("aria-valuenow", String(progress));
    progressOuter.setAttribute("aria-valuemin", "0");
    progressOuter.setAttribute("aria-valuemax", "100");
    const progressBar = document.createElement("div");
    progressBar.className = "progress-bar";
    progressBar.style.width = `${progress}%`;
    progressBar.textContent = `${progress} %`;
    progressOuter.append(progressBar);

    const metrics = document.createElement("div");
    metrics.className = "row g-2 text-center";
    const totalTime = `${Math.floor(totalMinutes / 60)}:${String(totalMinutes % 60).padStart(2, "0")}`;
    [[relevantTasks.length, "Aufgaben"], [activeTasks, "In Arbeit"], [waitingTasks, "Wartend"], [totalTime, "Gebuchte Stunden"]].forEach(([value, label]) => {
      const column = document.createElement("div");
      column.className = "col-6 col-md-3";
      const box = document.createElement("div");
      box.className = "border rounded p-2";
      const metric = document.createElement("div");
      metric.className = "fs-5 fw-semibold";
      metric.textContent = String(value);
      const caption = document.createElement("div");
      caption.className = "small text-secondary";
      caption.textContent = label;
      box.append(metric, caption);
      column.append(box);
      metrics.append(column);
    });
    body.append(progressOuter, metrics);
    cockpit.append(header, body);

    const firstProjectCard = main.querySelector("section.card");
    if (firstProjectCard) firstProjectCard.before(cockpit);

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

    if (taskCards.length) {
      const controls = document.createElement("div");
      controls.className = "card my-3";
      const controlsBody = document.createElement("div");
      controlsBody.className = "card-body py-2";
      const controlsRow = document.createElement("div");
      controlsRow.className = "row g-2 align-items-center";

      const searchColumn = document.createElement("div");
      searchColumn.className = "col-md-6";
      const filterInput = document.createElement("input");
      filterInput.type = "search";
      filterInput.className = "form-control form-control-sm";
      filterInput.id = "project-task-filter";
      filterInput.placeholder = "Aufgaben, Beschreibung oder Ressource filtern …";
      searchColumn.append(filterInput);

      const statusColumn = document.createElement("div");
      statusColumn.className = "col-md-3";
      const statusSelect = document.createElement("select");
      statusSelect.className = "form-select form-select-sm";
      statusSelect.id = "project-task-status";
      const allOption = document.createElement("option");
      allOption.value = "";
      allOption.textContent = "Alle Status";
      statusSelect.append(allOption);
      Object.entries(statusLabels).forEach(([value, label]) => {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = label;
        statusSelect.append(option);
      });
      statusColumn.append(statusSelect);

      const buttonColumn = document.createElement("div");
      buttonColumn.className = "col-md-3";
      const buttonGroup = document.createElement("div");
      buttonGroup.className = "btn-group btn-group-sm w-100";
      const expandButton = document.createElement("button");
      expandButton.type = "button";
      expandButton.className = "btn btn-outline-secondary";
      expandButton.id = "project-expand-all";
      expandButton.textContent = "Alle öffnen";
      const collapseButton = document.createElement("button");
      collapseButton.type = "button";
      collapseButton.className = "btn btn-outline-secondary";
      collapseButton.id = "project-collapse-all";
      collapseButton.textContent = "Alle zuklappen";
      buttonGroup.append(expandButton, collapseButton);
      buttonColumn.append(buttonGroup);

      controlsRow.append(searchColumn, statusColumn, buttonColumn);
      const count = document.createElement("div");
      count.className = "small text-secondary mt-2";
      count.id = "project-task-filter-count";
      controlsBody.append(controlsRow, count);
      controls.append(controlsBody);

      const newTask = document.getElementById("new-task");
      if (newTask) newTask.after(controls);
      else taskSection.prepend(controls);

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

      expandButton.addEventListener("click", () => {
        taskData.forEach((task) => task.card.querySelector(".card-body")?.classList.remove("d-none"));
      });
      collapseButton.addEventListener("click", () => {
        taskData.forEach((task) => task.card.querySelector(".card-body")?.classList.add("d-none"));
      });
    }

    const title = main.querySelector("h1")?.textContent.trim() || "Projekt";
    const summaryButton = document.createElement("button");
    summaryButton.type = "button";
    summaryButton.className = "btn btn-sm btn-outline-secondary ms-2";
    const copyIcon = document.createElement("i");
    copyIcon.className = "fas fa-copy me-1";
    copyIcon.setAttribute("aria-hidden", "true");
    summaryButton.append(copyIcon, document.createTextNode("Zusammenfassung kopieren"));
    cockpit.querySelector(".card-header")?.append(summaryButton);

    summaryButton.addEventListener("click", async () => {
      const lines = [
        title,
        `Fortschritt: ${progress} % (${completedTasks}/${relevantTasks.length} Aufgaben erledigt)`,
        `In Arbeit: ${activeTasks} · Wartend: ${waitingTasks}`,
        `Gebuchte Zeit: ${totalTime} h`,
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
        summaryButton.textContent = "Kopiert";
        setTimeout(() => {
          const iconElement = document.createElement("i");
          iconElement.className = "fas fa-copy me-1";
          iconElement.setAttribute("aria-hidden", "true");
          summaryButton.replaceChildren(iconElement, document.createTextNode("Zusammenfassung kopieren"));
        }, 1500);
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