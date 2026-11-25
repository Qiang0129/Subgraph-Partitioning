package local.subfigure;

import org.springframework.stereotype.Service;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.http.HttpStatus;
import java.nio.file.*;
import java.time.Instant;
import java.util.*;
import java.util.concurrent.CopyOnWriteArrayList;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

@Service
@SuppressWarnings("unchecked")
public class Workbench {
    final Store store; final AgentClient agent;
    final List<SseEmitter> listeners=new CopyOnWriteArrayList<>();
    static final Set<String> ACCEPTED=Set.of("CONFIRMED","AUTO_ACCEPTED");
    Workbench(Store store,AgentClient agent) { this.store=store; this.agent=agent; }
    static void require(boolean condition,String message) {
        if(!condition) throw new ResponseStatusException(HttpStatus.BAD_REQUEST,message);
    }
    static int integer(Object value,int fallback) { return value instanceof Number n?n.intValue():fallback; }
    Map<String,Object> provider(Map<String,Object> body) {
        String id=body.getOrDefault("id",Store.id()).toString();
        Map<String,Object> previous=body.containsKey("id")?store.get("provider",id):Map.of();
        var value=new LinkedHashMap<String,Object>();
        value.put("id",id);
        for(String key:List.of("name","base_url","model","protocol")) {
            String entry=body.getOrDefault(key,"").toString().trim(); require(!entry.isEmpty(),"缺少配置字段："+key); value.put(key,entry);
        }
        require(Set.of("chat","responses").contains(value.get("protocol")),"不支持的模型协议");
        String effort=Objects.toString(body.getOrDefault("reasoning_effort",previous.getOrDefault("reasoning_effort","default")),"");
        require(Set.of("default","none","minimal","low","medium","high","xhigh","max").contains(effort),"不支持的思考程度");
        value.put("reasoning_effort",effort);
        String key=body.getOrDefault("api_key","").toString();
        require(!key.isEmpty()||previous.containsKey("key_cipher"),"请填写 API Key");
        value.put("key_cipher",key.isEmpty()?previous.get("key_cipher"):store.encrypt(key));
        value.put("timeout_seconds",Math.clamp(integer(body.get("timeout_seconds"),90),5,300));
        value.put("verified",false); value.put("updated_at",Instant.now().toString());
        store.put("provider",id,value); return store.publicProvider(value);
    }
    Map<String,Object> providerModels(Map<String,Object> body) {
        String base=Objects.toString(body.get("base_url"),"").trim();
        require(!base.isEmpty(),"请先填写接口地址");
        String key=Objects.toString(body.get("api_key"),"");
        String id=Objects.toString(body.get("id"),"");
        if(key.isBlank()&&!id.isBlank()) {
            var previous=store.get("provider",id);
            require(base.replaceAll("/+$","").equals(previous.get("base_url").toString().replaceAll("/+$","")),"接口地址已改变，请重新填写 API Key 后获取模型列表");
            require(previous.containsKey("key_cipher"),"请先填写 API Key");
            key=store.decrypt(previous.get("key_cipher").toString());
        }
        require(!key.isBlank(),"请先填写 API Key");
        return agent.call("POST","/models",Map.of("base_url",base,"api_key",key,"timeout_seconds",30));
    }
    Map<String,Object> probe(String id) {
        var p=store.get("provider",id); var result=agent.call("POST","/probe",store.providerSecret(p));
        var current=store.get("provider",id);
        require(current.get("updated_at").equals(p.get("updated_at")),"配置已变化，请重新测试");
        current.put("verified",true); current.put("probe",result); store.put("provider",id,current); return store.publicProvider(current);
    }
    synchronized Map<String,Object> createBatch(Map<String,Object> body) {
        var batch=new LinkedHashMap<String,Object>(); String id=Store.id();
        batch.put("id",id); batch.put("name",body.getOrDefault("name","新批次")); batch.put("created_at",Instant.now().toString());
        store.put("batch",id,batch); return batch;
    }
    synchronized List<Map<String,Object>> upload(String batchId,MultipartFile[] files) throws Exception {
        store.get("batch",batchId);
        long existing=store.list("image").stream().filter(i->batchId.equals(i.get("batch_id"))).count();
        require(files.length>0&&existing+files.length<=500,"每个批次最多 500 张图片");
        var result=new ArrayList<Map<String,Object>>();
        for(var file:files) {
            String name=Path.of(Objects.requireNonNullElse(file.getOriginalFilename(),"image.png").replace('\\','/')).getFileName().toString();
            require(name.toLowerCase(Locale.ROOT).matches(".*\\.(png|jpe?g)$"),"仅支持 PNG、JPEG");
            String id=Store.id(); Path dir=store.root.resolve("images").resolve(id); Files.createDirectories(dir);
            Files.createDirectories(dir.resolve("original"));
            Path original=dir.resolve("original").resolve(name); file.transferTo(original);
            var image=new LinkedHashMap<String,Object>();
            image.put("id",id); image.put("batch_id",batchId); image.put("name",name); image.put("revision",0);
            image.put("source",dir.resolve("normalized.png").toString()); image.put("original",original.toString()); image.put("status","DRAFT");
            try {
                var metadata=agent.call("POST","/normalize",Map.of("source",original.toString(),"target",image.get("source")));
                image.putAll(metadata); image.put("layout",Map.of("schema_version","2.0","image_size",metadata.get("image_size"),"panels",List.of(),"shared_regions",List.of()));
            } catch(Exception error) { image.put("status","FAILED"); image.put("error",error.getMessage()); }
            store.put("image",id,image); result.add(publicImage(image,false));
        }
        return result;
    }
    Map<String,Object> publicImage(Map<String,Object> image,boolean details) {
        var value=new LinkedHashMap<>(image);
        for(String key:List.of("source","original","candidate_dir","provider_snapshot","resume_mode","feedback")) value.remove(key);
        if(!details) for(String key:List.of("layout","issues","events","usage","versions")) value.remove(key);
        else {
            var versions=(List<Map<String,Object>>)value.getOrDefault("versions",List.of());
            value.put("versions",versions.stream().map(v->Map.of("revision",v.get("revision"),"status",v.get("status"),"created_at",v.get("created_at"))).toList());
        }
        return value;
    }
    synchronized void startBatch(String id,Map<String,Object> body) {
        store.get("batch",id);
        var provider=store.get("provider",body.getOrDefault("provider_id","").toString());
        require(Boolean.TRUE.equals(provider.get("verified")),"请先通过模型能力测试");
        for(var image:store.list("image")) if(id.equals(image.get("batch_id"))&&!Set.of("RUNNING","CANCELLING","CONFIRMED","AUTO_ACCEPTED").contains(image.get("status"))) {
            require(image.containsKey("layout"),"批次含无法解码的图片，请先处理失败项");
            image.put("provider_snapshot",provider); image.put("status","QUEUED"); image.put("resume_mode",false);
            image.put("max_calls",Math.clamp(integer(body.get("max_calls"),24),4,100));
            image.put("max_repairs",Math.clamp(integer(body.get("max_repairs"),3),0,10));
            image.put("max_seconds",Math.clamp(integer(body.get("max_seconds"),600),30,3600));
            store.put("image",image.get("id").toString(),image);
        }
    }
    Map<String,Object> request(Map<String,Object> image) {
        var value=new LinkedHashMap<String,Object>();
        value.put("image_id",image.get("id")); value.put("source",image.get("source"));
        value.put("output_dir",store.root.resolve("images").resolve(image.get("id").toString()).resolve("agent").toString());
        value.put("provider",store.providerSecret((Map<String,Object>)image.get("provider_snapshot")));
        for(String key:List.of("max_calls","max_repairs","max_seconds")) if(image.containsKey(key)) value.put(key,image.get(key));
        return value;
    }
    synchronized void retry(String id,Map<String,Object> body) {
        var image=store.get("image",id); editable(image);
        require(image.containsKey("provider_snapshot"),"请先使用已验证模型启动批次");
        image.put("status","QUEUED"); image.put("resume_mode",true); image.put("feedback",body.getOrDefault("feedback","重新检查边界"));
        store.put("image",id,image);
    }
    synchronized void cancelBatch(String id) {
        for(var image:store.list("image")) if(id.equals(image.get("batch_id"))) {
            if("QUEUED".equals(image.get("status"))) image.put("status","CANCELLED");
            else if("RUNNING".equals(image.get("status"))) { agent.call("POST","/cancel/"+image.get("id"),Map.of()); image.put("status","CANCELLING"); }
            store.put("image",image.get("id").toString(),image);
        }
    }
    void editable(Map<String,Object> image) { require(!Set.of("RUNNING","QUEUED","CANCELLING").contains(image.get("status")),"运行中的图片不能被人工覆盖；请先取消并等待停止"); }
    synchronized Map<String,Object> saveLayout(String id,Map<String,Object> body) {
        var image=store.get("image",id); editable(image);
        if(integer(body.get("revision"),-1)!=integer(image.get("revision"),0)) throw new ResponseStatusException(HttpStatus.CONFLICT,"已有新版本，请刷新后重新编辑");
        boolean confirm=Boolean.TRUE.equals(body.get("confirm"));
        require(!confirm||Boolean.TRUE.equals(body.get("acknowledge_warnings")),"人工确认需明确承认已检查全部内容与警告");
        var result=agent.call("POST","/render",Map.of("source",image.get("source"),"output_dir",store.root.resolve("images").resolve(id).toString(),
            "layout",body.get("layout"),"confirm",confirm,"ocr",Boolean.TRUE.equals(body.get("ocr"))));
        applyVersion(image,result.get("directory").toString(),(Map<String,Object>)result.get("layout"),(Map<String,Object>)result.get("report"));
        store.put("image",id,image); return publicImage(image,true);
    }
    synchronized Map<String,Object> legacy(String id,int revision) {
        var image=store.get("image",id); editable(image);
        var layout=agent.call("POST","/legacy",Map.of("source",image.get("original"),"original_name",image.get("name")));
        return saveLayout(id,Map.of("revision",revision,"layout",layout,"confirm",false));
    }
    synchronized void gold(String id,Map<String,Object> body) {
        var image=store.get("image",id); require("CONFIRMED".equals(image.get("status")),"标准答案必须先人工确认");
        store.put("gold",id,Map.of("id",id,"name",image.get("name"),"sha256",image.get("sha256"),"layout",image.get("layout"),
            "confirmed_at",Instant.now().toString(),"notes",body.getOrDefault("notes",""),"critical_regions",body.getOrDefault("critical_regions",List.of())));
    }
    void applyVersion(Map<String,Object> image,String directory,Map<String,Object> layout,Map<String,Object> report) {
        store.safe(Path.of(directory));
        try {
            Path jsonPath=Path.of(directory).resolve("layout.json");
            var artifact=store.parse(Files.readString(jsonPath));
            var processing=(Map<String,Object>)artifact.getOrDefault("source",Map.of());
            artifact.put("source",Map.of("name",image.get("name"),"sha256",image.get("sha256"),
                "normalized_sha256",processing.getOrDefault("sha256",""),
                "original_size",image.getOrDefault("original_size",image.get("image_size")),
                "exif_orientation",image.getOrDefault("exif_orientation",1)));
            Files.writeString(jsonPath,store.json.writerWithDefaultPrettyPrinter().writeValueAsString(artifact));
            layout=artifact;
        } catch(Exception e) { throw new IllegalStateException("Could not save source provenance",e); }
        int revision=integer(image.get("revision"),0)+1;
        var versions=new ArrayList<>((List<Map<String,Object>>)image.getOrDefault("versions",List.of()));
        versions.add(Map.of("revision",revision,"directory",directory,"status",report.getOrDefault("status","DRAFT"),"created_at",Instant.now().toString()));
        image.put("versions",versions); image.put("revision",revision); image.put("candidate_dir",directory);
        image.put("layout",layout); image.put("issues",report.getOrDefault("issues",List.of())); image.put("status",report.getOrDefault("status","DRAFT"));
    }
    @Scheduled(fixedDelay=1800) synchronized void tick() {
        int running=0;
        for(var image:store.list("image")) if(Set.of("RUNNING","CANCELLING").contains(image.get("status"))) {
            try {
                String id=image.get("id").toString(); var result=agent.call("GET","/jobs/"+id,null);
                for(String key:List.of("events","calls","usage","error","repairs")) if(result.containsKey(key)) image.put(key,result.get(key));
                if(Boolean.TRUE.equals(result.get("active"))) running++;
                else {
                    String directory=result.getOrDefault("candidate_dir","").toString();
                    if(!directory.isBlank()&&!directory.equals(image.get("candidate_dir"))) applyVersion(image,directory,(Map<String,Object>)result.get("layout"),
                        Map.of("status",result.getOrDefault("status","NEEDS_REVIEW"),"issues",result.getOrDefault("issues",List.of())));
                    image.put("status","CANCELLING".equals(image.get("status"))?"CANCELLED":result.getOrDefault("status","INTERRUPTED"));
                }
                store.put("image",id,image);
            } catch(Exception error) {
                int failures=integer(image.get("poll_failures"),0)+1; image.put("poll_failures",failures);
                if(failures>=3) { image.put("status","INTERRUPTED"); image.put("error","Agent 连接中断，可恢复任务"); } else running++;
                store.put("image",image.get("id").toString(),image);
            }
        }
        for(var image:store.list("image")) if(running<2&&"QUEUED".equals(image.get("status"))) {
            try {
                agent.call("POST","/run",Map.of("request",request(image),"mode",Boolean.TRUE.equals(image.get("resume_mode"))?"resume":"run",
                    "decision",Map.of("action","retry","feedback",image.getOrDefault("feedback","重新检查"),"layout",image.get("layout"))));
                image.put("status","RUNNING"); image.put("poll_failures",0); running++;
            } catch(Exception error) { image.put("status","FAILED"); image.put("error",error.getMessage()); }
            store.put("image",image.get("id").toString(),image);
        }
        for(var emitter:listeners) try { emitter.send(SseEmitter.event().name("tick").data(System.currentTimeMillis())); }
            catch(Exception e) { listeners.remove(emitter); }
    }
}
