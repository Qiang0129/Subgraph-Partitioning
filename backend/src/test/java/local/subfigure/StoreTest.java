package local.subfigure;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import java.util.Map;
import static org.junit.jupiter.api.Assertions.*;
class StoreTest {
    @Test void providerPublicProjectionDoesNotExposeKeyMaterial() {
        var store=new Store(new ObjectMapper(),null);
        var result=store.publicProvider(Map.of("id","test","api_key","fixture","key_cipher","cipher"));
        assertFalse(result.containsKey("api_key")); assertFalse(result.containsKey("key_cipher")); assertEquals(true,result.get("has_key"));
    }
    @Test void windowsDpapiRoundTrip() {
        var store=new Store(new ObjectMapper(),null);String value="synthetic-test-key";
        String encrypted=store.encrypt(value);assertNotEquals(value,encrypted);assertEquals(value,store.decrypt(encrypted));
    }
}
