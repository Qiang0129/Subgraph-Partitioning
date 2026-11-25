package local.subfigure;

import org.springframework.stereotype.Component;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;
import java.net.URI;
import java.net.http.*;
import java.time.Duration;
import java.util.*;

@Component
public class AgentClient {
    final Store store;
    final HttpClient client=HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(5)).build();
    final String base=System.getenv().getOrDefault("SFA_AGENT_URL","http://127.0.0.1:8766");
    AgentClient(Store store) { this.store=store; }
    Map<String,Object> call(String method,String path,Object payload) {
        try {
            var builder=HttpRequest.newBuilder(URI.create(base+path)).timeout(Duration.ofSeconds(path.equals("/probe")?250:180))
                .header("X-Internal-Token",System.getenv().getOrDefault("SFA_INTERNAL_TOKEN",""));
            if(method.equals("GET")) builder.GET();
            else builder.header("Content-Type","application/json").POST(HttpRequest.BodyPublishers.ofString(store.json.writeValueAsString(payload)));
            var response=client.send(builder.build(),HttpResponse.BodyHandlers.ofString());
            if(response.statusCode()>=400) {
                String message="Agent 请求失败（HTTP "+response.statusCode()+"）";
                try { var error=store.parse(response.body()); if(error.get("detail") instanceof String detail) message=detail; } catch(Exception ignored) {}
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST,message.substring(0,Math.min(500,message.length())));
            }
            return store.parse(response.body());
        } catch(ResponseStatusException e) { throw e; }
        catch(Exception e) { throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE,"Agent 服务暂不可用，请检查启动日志"); }
    }
}
