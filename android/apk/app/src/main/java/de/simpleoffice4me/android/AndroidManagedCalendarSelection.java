package de.simpleoffice4me.android;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/** Captures the exact calendar ids and visible labels approved in the Android sync dialog. */
final class AndroidManagedCalendarSelection {
    static final int MAX_JSON_CHARS = 8192;
    record Item(String id, String name) {}

    private AndroidManagedCalendarSelection() {}

    static List<Item> validate(List<Item> submitted) {
        if (submitted.size() > 8) throw new IllegalArgumentException("Select at most eight calendars");
        List<Item> selected = new ArrayList<>();
        for (Item item : submitted) {
            if (item == null || item.id() == null || item.name() == null)
                throw new IllegalArgumentException("Invalid calendar selection");
            String id = item.id();
            if (!id.equals("simpleoffice:birthdays") && !id.matches("[a-z0-9][a-z0-9-]{0,62}"))
                throw new IllegalArgumentException("Invalid calendar identifier");
            String name = item.name().replaceAll("[\\p{Cc}\\p{Cf}]", " ").trim();
            if (name.length() > 120) name = name.substring(0, 120).trim();
            selected.add(new Item(id, name.isEmpty() ? id : name));
        }
        return Collections.unmodifiableList(selected);
    }

    static List<String> names(List<Item> selected) {
        List<String> names = new ArrayList<>(selected.size());
        for (Item item : selected) names.add(item.name());
        return Collections.unmodifiableList(names);
    }
}
