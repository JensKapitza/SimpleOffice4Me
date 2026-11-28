package de.simpleoffice4me.android;

import java.util.List;

/** Text shown before asking Android for write permission. */
final class AndroidManagedSyncPreview {
    private AndroidManagedSyncPreview() {}

    static String calendarScope(int count, List<String> names) {
        StringBuilder scope = new StringBuilder().append(count).append(" Termine/Serienausnahmen in den Kalendern:");
        for (String name : names) scope.append("\n• ").append(name);
        return scope.toString();
    }
}
