package de.simpleoffice4me.android;

import java.util.regex.Pattern;

/** Validates vCard-style contact dates without inventing a birth year. */
final class AndroidManagedContactsBirthdays {
    private static final Pattern WITH_YEAR = Pattern.compile("[0-9]{4}-[0-9]{2}-[0-9]{2}");
    private static final Pattern WITHOUT_YEAR = Pattern.compile("--[0-9]{2}-[0-9]{2}");
    private static final int[] MONTH_DAYS = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};

    private AndroidManagedContactsBirthdays() {}

    static String validate(String value) {
        if (value == null || value.isEmpty()) {
            return value;
        }
        boolean yearless = WITHOUT_YEAR.matcher(value).matches();
        if (!yearless && !WITH_YEAR.matcher(value).matches()) {
            throw new IllegalArgumentException("Invalid contact birthday");
        }
        int year = yearless ? 2000 : Integer.parseInt(value.substring(0, 4));
        int monthStart = yearless ? 2 : 5;
        int month = Integer.parseInt(value.substring(monthStart, monthStart + 2));
        int day = Integer.parseInt(value.substring(monthStart + 3));
        if (year < 1 || month < 1 || month > 12 || day < 1) {
            throw new IllegalArgumentException("Invalid contact birthday");
        }
        boolean leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
        int maxDay = month == 2 && leap ? 29 : MONTH_DAYS[month - 1];
        if (day > maxDay) {
            throw new IllegalArgumentException("Invalid contact birthday");
        }
        return value;
    }
}
