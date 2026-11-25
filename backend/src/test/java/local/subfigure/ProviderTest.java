package local.subfigure;

import org.junit.jupiter.api.Test;
import org.springframework.web.server.ResponseStatusException;
import java.util.Map;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class ProviderTest {
    @Test void reasoningSavedAndChangingConfigurationRequiresProbe() {
        var store=mock(Store.class); var agent=mock(AgentClient.class);
        when(store.get("provider","existing")).thenReturn(Map.of("key_cipher","cipher","reasoning_effort","high"));
        when(store.publicProvider(anyMap())).thenAnswer(call->call.getArgument(0));
        var work=new Workbench(store,agent);
        var result=work.provider(Map.of("id","existing","name","fixture","base_url","http://fixture.invalid/v1","model","fixture","protocol","responses","reasoning_effort","xhigh"));
        assertEquals("xhigh",result.get("reasoning_effort"));
        assertEquals(false,result.get("verified"));
        assertEquals("cipher",result.get("key_cipher"));
        verify(store).put(eq("provider"),eq("existing"),anyMap());
    }
    @Test void savedKeyCanListModelsWithoutReturningSecret() {
        var store=mock(Store.class); var agent=mock(AgentClient.class);
        when(store.get("provider","existing")).thenReturn(Map.of("base_url","http://fixture.invalid/v1","key_cipher","cipher"));
        when(store.decrypt("cipher")).thenReturn("synthetic-key");
        when(agent.call(eq("POST"),eq("/models"),anyMap())).thenReturn(Map.of("models",java.util.List.of("vision-model")));
        var result=new Workbench(store,agent).providerModels(Map.of("id","existing","base_url","http://fixture.invalid/v1/","api_key",""));
        assertFalse(result.toString().contains("synthetic-key"));
        verify(agent).call("POST","/models",Map.of("base_url","http://fixture.invalid/v1/","api_key","synthetic-key","timeout_seconds",30));
        verify(store,never()).put(anyString(),anyString(),anyMap());
    }
    @Test void changingUnsavedUrlDoesNotForwardStoredKey() {
        var store=mock(Store.class); var agent=mock(AgentClient.class);
        when(store.get("provider","existing")).thenReturn(Map.of("base_url","http://fixture.invalid/v1","key_cipher","cipher"));
        assertThrows(ResponseStatusException.class,()->new Workbench(store,agent).providerModels(Map.of("id","existing","base_url","http://other.invalid/v1")));
        verifyNoInteractions(agent);
        verify(store,never()).decrypt(anyString());
    }
    @Test void invalidEffortRejectedBeforeSaving() {
        var store=mock(Store.class); var agent=mock(AgentClient.class);
        assertThrows(ResponseStatusException.class,()->new Workbench(store,agent).provider(Map.of("name","fixture","base_url","http://fixture.invalid/v1","model","fixture","protocol","chat","reasoning_effort","guess","api_key","synthetic")));
        verify(store,never()).put(anyString(),anyString(),anyMap());
    }
}
