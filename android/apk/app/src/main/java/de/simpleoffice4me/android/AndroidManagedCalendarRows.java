package de.simpleoffice4me.android;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TimeZone;

/** Bounded projection from the authenticated SimpleOffice calendar endpoint. */
final class AndroidManagedCalendarRows {
    private static final int MAX_EVENTS = 100;
    private static final int TEXT_BUDGET = 192 * 1024;
    private static final long DAY_MILLIS = 86_400_000L;
    private static final long MIN_TIMESTAMP = -62_135_596_800_000L; // year 1
    private static final long MAX_TIMESTAMP = 253_402_300_799_999L; // year 9999
    private static final Set<String> TIME_ZONES = new HashSet<>(Arrays.asList(TimeZone.getAvailableIDs()));

    private AndroidManagedCalendarRows() {}

    record Key(String calendar, String event) {}

    record Event(String id, String calendarId, String calendarName, String calendarZone, String title,
                 String description, long start, long end, String zone, boolean allDay,
                 String rrule, String rdate, String exdate, String originalId, long originalStart,
                 boolean cancelled) {
        Key key() { return new Key(calendarId, id); }
        Key originalKey() { return new Key(calendarId, originalId); }
        boolean repeats() { return present(rrule) || present(rdate); }
        String duration() { return encodeDuration(start, end, allDay); }
    }

    static List<Event> parse(JSONArray rows) throws JSONException {
        if (rows == null || rows.length() > MAX_EVENTS) {
            throw new IllegalArgumentException("Unexpected number of managed calendar events");
        }
        List<Event> events = new ArrayList<>(rows.length());
        Map<Key, Event> byKey = new HashMap<>();
        Map<String, String> calendarNames = new HashMap<>();
        Map<String, String> calendarZones = new HashMap<>();
        int bytesEstimate = 0;
        for (int index = 0; index < rows.length(); index++) {
            Event event = event(rows.getJSONObject(index));
            if (byKey.put(event.key(), event) != null) throw new IllegalArgumentException("Duplicate calendar event ID");
            String previousName = calendarNames.putIfAbsent(event.calendarId(), event.calendarName());
            String previousZone = calendarZones.putIfAbsent(event.calendarId(), event.calendarZone());
            if ((previousName != null && !previousName.equals(event.calendarName()))
                    || (previousZone != null && !previousZone.equals(event.calendarZone()))) {
                throw new IllegalArgumentException("Conflicting calendar metadata");
            }
            bytesEstimate += (event.id().length() + event.calendarId().length() + event.calendarName().length()
                    + event.calendarZone().length() + event.title().length() + event.description().length()
                    + event.zone().length() + event.rrule().length() + event.rdate().length()
                    + event.exdate().length() + event.originalId().length()) * 2;
            if (bytesEstimate > TEXT_BUDGET) throw new IllegalArgumentException("Managed calendar page is too large");
            events.add(event);
        }
        for (Event event : events) {
            if (event.originalId().isEmpty()) continue;
            Event localMaster = byKey.get(event.originalKey());
            if (localMaster != null && (!localMaster.repeats()
                    || (localMaster.allDay() && !midnight(event.originalStart())))) {
                throw new IllegalArgumentException("Invalid recurring calendar exception");
            }
        }
        return events;
    }

    static List<List<Event>> prepare(List<JSONArray> pages) throws JSONException {
        List<List<Event>> validated = new ArrayList<>();
        for (JSONArray page : pages) validated.add(parse(page));
        validateSnapshot(validated);
        return validated;
    }

    static void validateSnapshot(List<List<Event>> pages) {
        Map<Key, Event> seen = new HashMap<>();
        Map<String, String> names = new HashMap<>();
        Map<String, String> zones = new HashMap<>();
        for (List<Event> events : pages) {
            for (Event event : events) {
                String previousName = names.putIfAbsent(event.calendarId(), event.calendarName());
                String previousZone = zones.putIfAbsent(event.calendarId(), event.calendarZone());
                if ((previousName != null && !previousName.equals(event.calendarName()))
                        || (previousZone != null && !previousZone.equals(event.calendarZone()))
                        || seen.putIfAbsent(event.key(), event) != null) {
                    throw new IllegalArgumentException("Conflicting managed calendar snapshot");
                }
                if (event.originalId().isEmpty()) continue;
                Event master = seen.get(event.originalKey());
                if (master == null || !master.repeats()
                        || (master.allDay() && !midnight(event.originalStart()))) {
                    throw new IllegalArgumentException("Invalid ordering of calendar exceptions");
                }
            }
        }
    }

    private static Event event(JSONObject row) {
        String id = id(row, "id", true);
        String calendarId = id(row, "calendar_id", true);
        String name = text(row, "calendar_name", 160, true, false);
        boolean allDay = bool(row, "all_day");
        String eventZone = allDay ? "UTC" : zone(text(row, "timezone", 80, false, false));
        String calendarZone = zone(text(row, "calendar_timezone", 80, false, false));
        String title = text(row, "title", 256, false, false);
        String description = text(row, "description", 4096, false, true);
        String rrule = text(row, "rrule", 512, false, false);
        String rdate = text(row, "rdate", 2048, false, false);
        String exdate = text(row, "exdate", 2048, false, false);
        String master = id(row, "original_id", false);
        boolean cancelled = bool(row, "cancelled");
        long originalStart = master.isEmpty() ? 0 : millis(row, "original_start_ms");
        long start = cancelled && !master.isEmpty() && row.isNull("start_ms")
                ? originalStart : millis(row, "start_ms");
        long end = cancelled && !master.isEmpty() && row.isNull("end_ms")
                ? start + (allDay ? DAY_MILLIS : 0) : millis(row, "end_ms");
        boolean recurring = present(rrule) || present(rdate);
        if (end > MAX_TIMESTAMP || end < start
                || (recurring && !allDay && (end - start + 999) / 1000 > Integer.MAX_VALUE)
                || (allDay && (end == start || !midnight(start) || !midnight(end)))
                || (!master.isEmpty() && (id.equals(master) || present(rrule) || present(rdate) || present(exdate)))) {
            throw new IllegalArgumentException("Invalid managed calendar event dates or recurrence");
        }
        return new Event(id, calendarId, name, calendarZone, title, description, start, end, eventZone,
                allDay, rrule, rdate, exdate, master, originalStart, cancelled);
    }

    private static String zone(String text) {
        if (text.isEmpty()) return "UTC";
        if (TIME_ZONES.contains(text) || text.matches("GMT[+-](0[0-9]|1[0-9]|2[0-3]):[0-5][0-9]")) return text;
        throw new IllegalArgumentException("Unsupported managed calendar time zone");
    }

    private static String id(JSONObject row, String key, boolean required) {
        String id = text(row, key, 160, required, false);
        if (!id.isEmpty() && !id.matches("[A-Za-z0-9][A-Za-z0-9_.:@+~-]*")) {
            throw new IllegalArgumentException("Invalid managed calendar ID");
        }
        return id;
    }

    private static String text(JSONObject row, String key, int max, boolean required, boolean multiline) {
        Object raw = row.opt(key);
        if (raw == null || raw == JSONObject.NULL) {
            if (!required) return "";
            throw new IllegalArgumentException("Missing managed calendar field");
        }
        if (!(raw instanceof String str) || str.length() > max || (required && str.trim().isEmpty())) {
            throw new IllegalArgumentException("Invalid managed calendar field");
        }
        for (int pos = 0; pos < str.length(); pos++) {
            char c = str.charAt(pos);
            if (Character.isISOControl(c) && !(multiline && (c == '\n' || c == '\r' || c == '\t'))) {
                throw new IllegalArgumentException("Invalid managed calendar text");
            }
        }
        return str;
    }

    private static boolean bool(JSONObject row, String key) {
        Object value = row.opt(key);
        if (value == null || value == JSONObject.NULL) return false;
        if (value instanceof Boolean result) return result;
        throw new IllegalArgumentException("Invalid managed calendar flag");
    }

    private static long millis(JSONObject row, String key) {
        Object value = row.opt(key);
        if (!(value instanceof Integer || value instanceof Long)) {
            throw new IllegalArgumentException("Missing integer calendar timestamp");
        }
        long millis = ((Number) value).longValue();
        if (millis < MIN_TIMESTAMP || millis > MAX_TIMESTAMP) {
            throw new IllegalArgumentException("Managed calendar timestamp is out of range");
        }
        return millis;
    }

    static boolean midnight(long instant) {
        return instant % DAY_MILLIS == 0;
    }

    static boolean present(String text) {
        return text != null && !text.isEmpty();
    }

    // CalendarProvider can consult an original sync ID without calendar/account context.
    // Namespace every provider event ID across local principals and calendars.
    static String providerEventId(String account, String calendar, String event) {
        try {
            byte[] bytes = MessageDigest.getInstance("SHA-256")
                    .digest((account + "\0" + calendar + "\0" + event).getBytes(StandardCharsets.UTF_8));
            StringBuilder result = new StringBuilder("simpleoffice-v1-");
            for (byte value : bytes) {
                result.append(Character.forDigit((value & 0xff) >>> 4, 16));
                result.append(Character.forDigit(value & 0xf, 16));
            }
            return result.toString();
        } catch (NoSuchAlgorithmException impossible) {
            throw new IllegalStateException("SHA-256 is unavailable", impossible);
        }
    }

    static String encodeDuration(long start, long end, boolean allDay) {
        long millis = end - start;
        return allDay ? "P" + (millis / DAY_MILLIS) + "D" : "PT" + ((millis + 999) / 1000) + "S";
    }
}
