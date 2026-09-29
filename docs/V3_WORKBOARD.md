# V3 Workboard

The workboard is an additive projection over the existing CalDAV/VEVENT and VTODO stores. It does not create a third task/calendar model and never converts an event into a task or a task into an event.

- Events are read through \`CalendarStore.occurrences\`.
- Tasks are read through \`TodoStore.items\`.
- Planning an event calls \`CalendarStore.update\`.
- Planning a task calls \`TodoStore.update\`.
- Existing CalDAV/VTODO endpoints remain authoritative and unchanged.
- Filters are applied after each store has performed its normal actor visibility checks.
- Overlapping bounded intervals are shown as hints only; there is no forced scheduler.
- Tasks without start/due remain visible as unscheduled work.
- The window is bounded to 62 days in the service and 31 days in the browser UI.

The feature is disabled by default through \`v3.workboard\`. The old task and calendar views remain available irrespective of the flag.
