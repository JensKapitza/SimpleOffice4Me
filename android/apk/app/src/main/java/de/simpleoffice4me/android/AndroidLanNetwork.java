package de.simpleoffice4me.android;

import android.content.Context;
import android.net.ConnectivityManager;
import android.net.LinkAddress;
import android.net.LinkProperties;
import android.net.Network;
import android.net.NetworkCapabilities;

import java.net.Inet4Address;
import java.net.InetAddress;
import java.util.LinkedHashSet;
import java.util.Set;

/**
 * Supplies private IPv4 addresses for the active local Android network.
 *
 * Only Wi-Fi and Ethernet transports are eligible. Cellular/VPN addresses are
 * deliberately not promoted to federation scan networks.
 */
final class AndroidLanNetwork {
    private AndroidLanNetwork() {
    }

    static String localPrivateIpv4(Context context) {
        ConnectivityManager manager =
                (ConnectivityManager) context.getSystemService(Context.CONNECTIVITY_SERVICE);
        if (manager == null) return "";

        try {
            Network network = manager.getActiveNetwork();
            if (network == null) return "";
            NetworkCapabilities capabilities = manager.getNetworkCapabilities(network);
            if (capabilities == null
                    || !(capabilities.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)
                    || capabilities.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET))) {
                return "";
            }

            LinkProperties properties = manager.getLinkProperties(network);
            if (properties == null) return "";
            Set<String> addresses = new LinkedHashSet<>();
            for (LinkAddress linkAddress : properties.getLinkAddresses()) {
                InetAddress address = linkAddress.getAddress();
                if (address instanceof Inet4Address && isRfc1918((Inet4Address) address)) {
                    addresses.add(address.getHostAddress());
                }
            }
            StringBuilder result = new StringBuilder();
            for (String address : addresses) {
                if (result.length() > 0) result.append(',');
                result.append(address);
            }
            return result.toString();
        } catch (RuntimeException ignored) {
            return "";
        }
    }

    private static boolean isRfc1918(Inet4Address address) {
        byte[] value = address.getAddress();
        int first = value[0] & 0xff;
        int second = value[1] & 0xff;
        return first == 10
                || (first == 172 && second >= 16 && second <= 31)
                || (first == 192 && second == 168);
    }
}
