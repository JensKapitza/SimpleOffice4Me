package de.simpleoffice4me.android;

import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.NetworkInterface;
import java.net.SocketException;
import java.util.Enumeration;
import java.util.LinkedHashSet;
import java.util.Set;

/**
 * Supplies every currently assigned non-loopback IPv4 address of the device.
 *
 * Hotspot/tethering interfaces are not necessarily Android's active network.
 * Enumerating the device interfaces therefore avoids transport and RFC1918
 * assumptions which would otherwise hide a usable hotspot address.
 */
final class AndroidLanNetwork {
    private AndroidLanNetwork() {
    }

    static String localIpv4Addresses() {
        Set<String> addresses = new LinkedHashSet<>();
        try {
            Enumeration<NetworkInterface> interfaces = NetworkInterface.getNetworkInterfaces();
            if (interfaces == null) return "";

            while (interfaces.hasMoreElements()) {
                NetworkInterface networkInterface = interfaces.nextElement();
                Enumeration<InetAddress> interfaceAddresses = networkInterface.getInetAddresses();
                while (interfaceAddresses.hasMoreElements()) {
                    InetAddress address = interfaceAddresses.nextElement();
                    if (address instanceof Inet4Address
                            && !address.isLoopbackAddress()
                            && !address.isAnyLocalAddress()) {
                        addresses.add(address.getHostAddress());
                    }
                }
            }
        } catch (SocketException | RuntimeException ignored) {
            return "";
        }

        StringBuilder result = new StringBuilder();
        for (String address : addresses) {
            if (result.length() > 0) result.append(',');
            result.append(address);
        }
        return result.toString();
    }
}
