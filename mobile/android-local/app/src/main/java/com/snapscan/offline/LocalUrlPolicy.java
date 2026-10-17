package com.snapscan.offline;

import java.net.URI;

/** Fixed local WebView origin. A rejected request must never fall back to a network loader. */
public final class LocalUrlPolicy {
    public static final String ORIGIN = "https://appassets.androidplatform.net";
    private LocalUrlPolicy() {}
    public static String appPath(String value) {
        try {
            URI uri = new URI(value);
            if (!"https".equalsIgnoreCase(uri.getScheme()) || !"appassets.androidplatform.net".equalsIgnoreCase(uri.getHost())
                    || uri.getUserInfo() != null || (uri.getPort() != -1 && uri.getPort() != 443)) return null;
            String path = uri.getPath();
            if (path == null || path.isEmpty()) path = "/";
            if (!path.startsWith("/") || path.contains("\\") || path.indexOf('\0') >= 0 || path.contains("%") || path.contains("//")) return null;
            for (String segment : path.split("/")) if (segment.equals("..") || segment.equals(".")) return null;
            return path;
        } catch (Exception error) { return null; }
    }
}
