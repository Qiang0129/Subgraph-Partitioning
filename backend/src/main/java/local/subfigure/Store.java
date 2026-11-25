package local.subfigure;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.jna.platform.win32.Crypt32Util;
import jakarta.annotation.PostConstruct;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.http.HttpStatus;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;

@Component
public class Store {
    final Path root = Path.of(System.getenv().getOrDefault("SFA_DATA", "runtime")).toAbsolutePath().normalize();
    final ObjectMapper json;
    final JdbcTemplate db;
    Store(ObjectMapper json, JdbcTemplate db) { this.json=json; this.db=db; }
    @PostConstruct void init() throws IOException {
        Files.createDirectories(root.resolve("images"));
        db.execute("PRAGMA journal_mode=WAL");
        db.execute("CREATE TABLE IF NOT EXISTS objects(kind TEXT NOT NULL,id TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(kind,id))");
        for(var image:list("image")) if(Set.of("RUNNING","CANCELLING").contains(image.get("status"))) {
            image.put("status","INTERRUPTED"); put("image",image.get("id").toString(),image);
        }
    }
    synchronized void put(String kind,String id,Map<String,Object> value) {
        try { db.update("INSERT INTO objects(kind,id,payload) VALUES(?,?,?) ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload",kind,id,json.writeValueAsString(value)); }
        catch(Exception e) { throw new IllegalStateException("Database write failed",e); }
    }
    Map<String,Object> parse(String data) {
        try { return json.readValue(data,new TypeReference<LinkedHashMap<String,Object>>(){}); }
        catch(Exception e) { throw new IllegalStateException("Invalid stored JSON",e); }
    }
    Map<String,Object> get(String kind,String id) {
        var found=db.queryForList("SELECT payload FROM objects WHERE kind=? AND id=?",String.class,kind,id);
        if(found.isEmpty()) throw new ResponseStatusException(HttpStatus.NOT_FOUND,"对象不存在");
        return parse(found.getFirst());
    }
    List<Map<String,Object>> list(String kind) { return db.queryForList("SELECT payload FROM objects WHERE kind=? ORDER BY rowid DESC",String.class,kind).stream().map(this::parse).toList(); }
    void delete(String kind,String id) { db.update("DELETE FROM objects WHERE kind=? AND id=?",kind,id); }
    static String id() { return UUID.randomUUID().toString().replace("-",""); }
    Path safe(Path path) {
        Path resolved=path.toAbsolutePath().normalize();
        if(!resolved.startsWith(root)) throw new ResponseStatusException(HttpStatus.BAD_REQUEST,"无效文件路径");
        return resolved;
    }
    String encrypt(String key) { return Base64.getEncoder().encodeToString(Crypt32Util.cryptProtectData(key.getBytes(StandardCharsets.UTF_8))); }
    String decrypt(String cipher) { return new String(Crypt32Util.cryptUnprotectData(Base64.getDecoder().decode(cipher)),StandardCharsets.UTF_8); }
    Map<String,Object> publicProvider(Map<String,Object> source) {
        var value=new LinkedHashMap<>(source); value.remove("key_cipher"); value.remove("api_key"); value.put("has_key",source.containsKey("key_cipher")); value.putIfAbsent("reasoning_effort","default"); return value;
    }
    Map<String,Object> providerSecret(Map<String,Object> source) {
        var value=new LinkedHashMap<>(source); value.put("api_key",decrypt(value.remove("key_cipher").toString())); return value;
    }
}
