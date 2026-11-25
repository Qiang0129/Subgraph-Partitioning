package local.subfigure;

import jakarta.servlet.http.HttpServletResponse;
import org.springframework.core.io.FileSystemResource;
import org.springframework.http.*;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;
import java.nio.file.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.zip.*;

@RestController
@RequestMapping("/api")
@SuppressWarnings("unchecked")
public class Api {
    final Store store; final Workbench work; final AgentClient agent;
    Api(Store store,Workbench work,AgentClient agent) { this.store=store; this.work=work; this.agent=agent; }
    @GetMapping("/health") Object health() {
        boolean ready; try { agent.call("GET","/health",null); ready=true; } catch(Exception e) { ready=false; }
        return Map.of("backend","ok","agent",ready,"name","Subgraph Partitioning");
    }
    @GetMapping("/providers") Object providers() { return store.list("provider").stream().map(store::publicProvider).toList(); }
    @PostMapping("/providers") Object saveProvider(@RequestBody Map<String,Object> body) { return work.provider(body); }
    @PostMapping("/providers/models") Object providerModels(@RequestBody Map<String,Object> body) { return work.providerModels(body); }
    @PostMapping("/providers/{id}/probe") Object probe(@PathVariable String id) { return work.probe(id); }
    @DeleteMapping("/providers/{id}") void deleteProvider(@PathVariable String id) { store.delete("provider",id); }
    @GetMapping("/batches") Object batches() { return store.list("batch"); }
    @PostMapping("/batches") Object batch(@RequestBody Map<String,Object> body) { return work.createBatch(body); }
    @GetMapping("/batches/{id}/images") Object images(@PathVariable String id) {
        return store.list("image").stream().filter(i->id.equals(i.get("batch_id"))).map(i->work.publicImage(i,false)).toList();
    }
    @PostMapping("/batches/{id}/images") Object upload(@PathVariable String id,@RequestParam("files") MultipartFile[] files) throws Exception { return work.upload(id,files); }
    @PostMapping("/batches/{id}/start") void start(@PathVariable String id,@RequestBody Map<String,Object> body) { work.startBatch(id,body); }
    @PostMapping("/batches/{id}/cancel") void cancel(@PathVariable String id) { work.cancelBatch(id); }
    @GetMapping("/images/{id}") Object image(@PathVariable String id) { return work.publicImage(store.get("image",id),true); }
    @PostMapping("/images/{id}/layout") Object save(@PathVariable String id,@RequestBody Map<String,Object> body) { return work.saveLayout(id,body); }
    @PostMapping("/images/{id}/legacy") Object legacy(@PathVariable String id,@RequestBody Map<String,Object> body) { return work.legacy(id,Workbench.integer(body.get("revision"),-1)); }
    @PostMapping("/images/{id}/retry") void retry(@PathVariable String id,@RequestBody Map<String,Object> body) { work.retry(id,body); }
    @PostMapping("/images/{id}/gold") void gold(@PathVariable String id,@RequestBody Map<String,Object> body) { work.gold(id,body); }
    @GetMapping("/gold") Object goldList() { return Map.of("schema_version","2.0","images",store.list("gold")); }
    @GetMapping(value="/events",produces=MediaType.TEXT_EVENT_STREAM_VALUE) SseEmitter events() {
        var emitter=new SseEmitter(600_000L); work.listeners.add(emitter);
        emitter.onCompletion(()->work.listeners.remove(emitter)); emitter.onTimeout(()->work.listeners.remove(emitter));
        return emitter;
    }
    Path directory(Map<String,Object> image,int version) {
        if(version<0) return store.safe(Path.of(image.get("candidate_dir").toString()));
        var versions=(List<Map<String,Object>>)image.getOrDefault("versions",List.of());
        return versions.stream().filter(v->Workbench.integer(v.get("revision"),0)==version).findFirst()
            .map(v->store.safe(Path.of(v.get("directory").toString()))).orElseThrow(()->new IllegalArgumentException("版本不存在"));
    }
    @GetMapping("/images/{id}/asset") ResponseEntity<FileSystemResource> asset(@PathVariable String id,@RequestParam(defaultValue="normalized.png") String file,@RequestParam(defaultValue="-1") int version) {
        var image=store.get("image",id);
        Path path=file.equals("normalized.png")?Path.of(image.get("source").toString()):directory(image,version).resolve(file).normalize();
        Workbench.require(path.startsWith(store.root.resolve("images").resolve(id))&&Files.isRegularFile(path),"文件不存在");
        Workbench.require(file.matches("[A-Za-z0-9_.-]+\\.(png|json)"),"无效产物名");
        return ResponseEntity.ok().contentType(file.endsWith(".png")?MediaType.IMAGE_PNG:MediaType.APPLICATION_JSON).body(new FileSystemResource(path));
    }
    @GetMapping("/batches/{id}/export") void export(@PathVariable String id,@RequestParam(defaultValue="false") boolean drafts,HttpServletResponse response) throws Exception {
        store.get("batch",id);
        response.setContentType("application/zip"); response.setHeader("Content-Disposition","attachment; filename=\"subfigures-"+id+".zip\"");
        var manifest=new ArrayList<Map<String,Object>>();
        try(var zip=new ZipOutputStream(response.getOutputStream(),StandardCharsets.UTF_8)) {
            for(var image:store.list("image")) if(id.equals(image.get("batch_id"))) {
                boolean accepted=Workbench.ACCEPTED.contains(image.get("status"));
                var entry=new LinkedHashMap<String,Object>();
                for(String key:List.of("id","name","sha256","status","revision","error")) if(image.containsKey(key)) entry.put(key,image.get(key));
                if(image.containsKey("candidate_dir")&&(accepted||drafts)) {
                    String prefix=(accepted?"accepted/":"draft/")+image.get("id")+"/"; entry.put("directory",prefix);
                    try(var files=Files.list(directory(image,-1))) {
                        for(Path file:files.filter(Files::isRegularFile).toList()) {
                            zip.putNextEntry(new ZipEntry(prefix+file.getFileName())); Files.copy(file,zip); zip.closeEntry();
                        }
                    }
                }
                manifest.add(entry);
            }
            zip.putNextEntry(new ZipEntry("manifest.json"));
            zip.write(store.json.writerWithDefaultPrettyPrinter().writeValueAsBytes(Map.of("schema_version","2.0","batch_id",id,"images",manifest)));
            zip.closeEntry();
        }
    }
    @ExceptionHandler(IllegalArgumentException.class) ResponseEntity<?> invalid(IllegalArgumentException e) { return ResponseEntity.badRequest().body(Map.of("message",e.getMessage())); }
}
