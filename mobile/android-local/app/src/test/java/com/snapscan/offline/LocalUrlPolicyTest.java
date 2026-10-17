package com.snapscan.offline;
import org.junit.Test;
import static org.junit.Assert.*;

public class LocalUrlPolicyTest {
    @Test public void permitsOnlyTheFixedVirtualHttpsOrigin() {
        assertEquals("/index.html", LocalUrlPolicy.appPath("https://appassets.androidplatform.net/index.html#/pages/index/index"));
        assertEquals("/uploads/scan.png", LocalUrlPolicy.appPath("https://appassets.androidplatform.net/uploads/scan.png"));
        assertNull(LocalUrlPolicy.appPath("http://appassets.androidplatform.net/index.html"));
        assertNull(LocalUrlPolicy.appPath("https://example.com/index.html"));
        assertNull(LocalUrlPolicy.appPath("https://user@appassets.androidplatform.net/index.html"));
        assertNull(LocalUrlPolicy.appPath("https://appassets.androidplatform.net:8443/index.html"));
        assertNull(LocalUrlPolicy.appPath("file:///data/data/com.snapscan.offline/secret"));
        assertNull(LocalUrlPolicy.appPath("javascript:alert(1)"));
    }
    @Test public void blocksTraversalAndDoubleEncodedPaths() {
        for (String suffix : new String[]{"/../secret", "/assets/./index.html", "/%2e%2e/secret", "/%252e%252e/secret", "/%5csecret", "//secret"})
            assertNull(suffix, LocalUrlPolicy.appPath(LocalUrlPolicy.ORIGIN + suffix));
    }
}
