(() => {
  "use strict";

  const text = (german, english) => window.SimpleOfficeI18n?.text(german, english) ?? english;

  function initProjectQuickWins() {
    const taskSection = document.getElementById("aufgaben");
    if (!taskSection) return;

    const main = taskSection.closest("main");
    if (!main || main.dataset.projectUi === "v2") return;
    const taskCards = Array.from(taskSection.querySelectorAll('article[id^="task-"]'));

    const statusLabels = {
      open: text("Offen", "Open"),
      in_progress: text("In Arbeit", "In progress"),
      waiting: text("Wartet", "Waiting"),
      completed: text("Erledigt", "Completed"),
      cancelled: text("Abgebrochen", "Cancelled")
    };

    const taskData = taskCards.map((card) => {
      const status = card.dataset.taskStatus || card.querySelector('.card-header .badge')?.textContent.trim() || "open";
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
    heading.textContent = text("Projektcockpit", "Project overview");
    const progressBadge = document.createElement("span");
    progressBadge.className = "badge text-bg-light border";
    progressBadge.textContent = text(`${progress} % erledigt`, `${progress}% complete`);
    header.append(heading, progressBadge);

    const body = document.createElement("div");
    body.className = "card-body";
    const progressOuter = document.createElement("div");
    progressOuter.className = "progress mb-3";
    progressOuter.setAttribute("role", "progressbar");
    progressOuter.setAttribute("aria-label", text("Projektfortschritt", "Project progress"));
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
    [[relevantTasks.length, text("Aufgaben", "Tasks")], [activeTasks, text("In Arbeit", "In progress")], [waitingTasks, text("Wartend", "Waiting")], [totalTime, text("Gebuchte Stunden", "Hours logged")]].forEach(([value, label]) => {
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
        alert.textContent = text(`Projekttermin seit ${Math.abs(days)} Tag(en) überschritten (${projectEndInput.value}).`, `Project deadline was ${Math.abs(days)} day(s) ago (${projectEndInput.value}).`);
      } else if (days <= 7) {
        alert.className = "alert alert-warning mb-4";
        alert.textContent = text(`Projekttermin in ${days} Tag(en) (${projectEndInput.value}).`, `Project deadline in ${days} day(s) (${projectEndInput.value}).`);
      } else {
        alert.className = "alert alert-light border mb-4";
        alert.textContent = text(`Geplantes Projektende: ${projectEndInput.value} · noch ${days} Tage.`, `Planned project end: ${projectEndInput.value} · ${days} days left.`);
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
      filterInput.placeholder = text("Aufgaben, Beschreibung oder Ressource filtern …", "Filter tasks, descriptions, or resources …");
      searchColumn.append(filterInput);

      const statusColumn = document.createElement("div");
      statusColumn.className = "col-md-3";
      const statusSelect = document.createElement("select");
      statusSelect.className = "form-select form-select-sm";
      statusSelect.id = "project-task-status";
      const allOption = document.createElement("option");
      allOption.value = "";
      allOption.textContent = text("Alle Status", "All statuses");
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
      expandButton.textContent = text("Alle öffnen", "Expand all");
      const collapseButton = document.createElement("button");
      collapseButton.type = "button";
      collapseButton.className = "btn btn-outline-secondary";
      collapseButton.id = "project-collapse-all";
      collapseButton.textContent = text("Alle zuklappen", "Collapse all");
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
        count.textContent = text(`${visible} von ${taskData.length} Aufgabe(n) sichtbar`, `${visible} of ${taskData.length} task(s) visible`);
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

    const title = main.querySelector("h1")?.textContent.trim() || text("Projekt", "Project");
    const summaryButton = document.createElement("button");
    summaryButton.type = "button";
    summaryButton.className = "btn btn-sm btn-outline-secondary ms-2";
    const copyIcon = document.createElement("i");
    copyIcon.className = "fas fa-copy me-1";
    copyIcon.setAttribute("aria-hidden", "true");
    const copyLabel = text("Zusammenfassung kopieren", "Copy summary");
    summaryButton.append(copyIcon, document.createTextNode(copyLabel));
    cockpit.querySelector(".card-header")?.append(summaryButton);

    summaryButton.addEventListener("click", async () => {
      const lines = [
        title,
        text(`Fortschritt: ${progress} % (${completedTasks}/${relevantTasks.length} Aufgaben erledigt)`, `Progress: ${progress}% (${completedTasks}/${relevantTasks.length} tasks completed)`),
        text(`In Arbeit: ${activeTasks} · Wartend: ${waitingTasks}`, `In progress: ${activeTasks} · Waiting: ${waitingTasks}`),
        text(`Gebuchte Zeit: ${totalTime} h`, `Time logged: ${totalTime} h`),
        projectEndInput?.value ? text(`Geplantes Ende: ${projectEndInput.value}`, `Planned end: ${projectEndInput.value}`) : text("Geplantes Ende: nicht gesetzt", "Planned end: not set"),
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
        summaryButton.textContent = text("Kopiert", "Copied");
        setTimeout(() => {
          const iconElement = document.createElement("i");
          iconElement.className = "fas fa-copy me-1";
          iconElement.setAttribute("aria-hidden", "true");
          summaryButton.replaceChildren(iconElement, document.createTextNode(copyLabel));
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
