/* POSIX capability-scoped I/O for standalone compiler execution. Roots are
   opened once at startup; every descendant is traversed relative to those fds. */
#include <fcntl.h>
#include <unistd.h>
#include <sys/stat.h>
#include <poll.h>

static PixelDecoded pixel_io_error(const char *code, const char *message) {
    return (PixelDecoded){.kind="io",.code=code,.message=message};
}
static PixelDecoded pixel_io_errno(int number) {
    const char *code="io.failure";
    switch (number) {
        case ENOENT: code="io.not_found"; break;
        case EACCES: case EPERM: case ELOOP: code="io.access_denied"; break;
        case ENOTDIR: code="io.invalid_path"; break;
        case EISDIR: code="io.invalid_file"; break;
        case ENOSPC: code="io.no_space"; break;
    }
    /* Stable diagnostics do not embed host paths or locale-dependent strerror. */
    return pixel_io_error(code,"File operation failed");
}
static size_t pixel_raw_scan(const unsigned char *bytes, size_t length, uint32_t *output) {
    size_t count=0;
    for (size_t i=0; i<length;) {
        uint32_t lead=bytes[i++],point; unsigned extra;
        if (lead<128) { point=lead; extra=0; }
        else if (lead>=0xc2 && lead<=0xdf) { point=lead&31; extra=1; }
        else if (lead>=0xe0 && lead<=0xef) { point=lead&15; extra=2; }
        else if (lead>=0xf0 && lead<=0xf4) { point=lead&7; extra=3; }
        else return SIZE_MAX;
        if (extra>length-i) return SIZE_MAX;
        for (unsigned j=0; j<extra; j++) {
            unsigned next=bytes[i++]; if (next<128 || next>191) return SIZE_MAX;
            point=(point<<6)|(next&63);
        }
        if ((extra==1 && point<128) || (extra==2 && point<2048) ||
            (extra==3 && point<65536) || point>0x10ffff || (point>=0xd800 && point<=0xdfff)) return SIZE_MAX;
        if (output) output[count]=point;
        count++;
    }
    return count;
}
static PixelDecoded pixel_raw_text(PixelContext *c, const unsigned char *raw, size_t length) {
    size_t count=pixel_raw_scan(raw,length,NULL);
    if (count==SIZE_MAX) return pixel_io_error("io.invalid_encoding","Invalid UTF-8 byte sequence");
    if (count>1000000) return pixel_io_error("io.too_large","File exceeds one million characters");
    PixelValue value=pixel_string_new(c,count);
    pixel_raw_scan(raw,length,(uint32_t *)value.string->data);
    return (PixelDecoded){.value=value};
}
static size_t pixel_raw_encode(PixelValue text, unsigned char *raw) {
    size_t n=0;
    for (size_t i=0; i<text.string->length; i++) {
        uint32_t p=text.string->data[i];
        if (p>0x10ffff || (p>=0xd800 && p<=0xdfff)) return SIZE_MAX;
        if (p<128) raw[n++]=(unsigned char)p;
        else if (p<2048) { raw[n++]=0xc0|(p>>6); raw[n++]=0x80|(p&63); }
        else if (p<65536) { raw[n++]=0xe0|(p>>12); raw[n++]=0x80|((p>>6)&63); raw[n++]=0x80|(p&63); }
        else { raw[n++]=0xf0|(p>>18); raw[n++]=0x80|((p>>12)&63); raw[n++]=0x80|((p>>6)&63); raw[n++]=0x80|(p&63); }
    }
    return n;
}
static PixelDecoded pixel_io_parent(PixelContext *c, PixelValue path, int writing, int *parent, char **name, char *buffer) {
    int root=writing ? c->write_root : c->read_root;
    if (root<0) return pixel_io_error("io.access_denied","File capability was not granted");
    if (!path.string->length || path.string->length>1024 || path.string->data[0]=='/')
        return pixel_io_error("io.invalid_path","File path must be relative and at most 1024 characters");
    for (size_t i=0; i<path.string->length; i++)
        if (!path.string->data[i] || path.string->data[i]=='\\')
            return pixel_io_error("io.invalid_path","Invalid file path character");
    size_t encoded=pixel_raw_encode(path,(unsigned char *)buffer);
    if (encoded==SIZE_MAX) return pixel_io_error("io.invalid_encoding","Invalid Unicode scalar in file path");
    buffer[encoded]=0;
    int fd=dup(root); if (fd<0) return pixel_io_errno(errno);
    char *part=buffer;
    for (;;) {
        char *slash=strchr(part,'/'); if (slash) *slash=0;
        if (!*part || !strcmp(part,".") || !strcmp(part,"..")) {
            close(fd); return pixel_io_error("io.invalid_path","File path must stay inside its granted root");
        }
        if (!slash) { *parent=fd; *name=part; return (PixelDecoded){0}; }
        int next=openat(fd,part,O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
        int error=errno; close(fd);
        if (next<0) return pixel_io_errno(error);
        fd=next; part=slash+1;
    }
}
static PixelDecoded pixel_io_read(PixelContext *c, int parent, const char *name, int text, int type, size_t limit) {
    int fd=openat(parent,name,O_RDONLY|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC);
    if (fd<0) return pixel_io_errno(errno);
    struct stat info;
    if (fstat(fd,&info)<0) { int error=errno; close(fd); return pixel_io_errno(error); }
    if (!S_ISREG(info.st_mode)) { close(fd); return pixel_io_error("io.invalid_file","Read requires a regular file"); }
    if (info.st_size<0 || (uint64_t)info.st_size>limit) { close(fd); return pixel_io_error("io.too_large","File exceeds read limit"); }
    if ((uint64_t)info.st_size>c->io_remaining) { close(fd); pixel_fail(c,"File operation budget exceeded"); }
    /* One extra byte detects growth beyond the limit, including empty files. */
    size_t capacity=(size_t)info.st_size+1,used=0;
    unsigned char *raw=malloc(capacity);
    if (!raw) { close(fd); pixel_fail(c,"Allocation failed"); }
    PixelDecoded result={0};
    for (;;) {
        if (pixel_cancelled) { free(raw); close(fd); pixel_fail(c,"Execution cancelled"); }
        if (used==capacity) {
            size_t next=capacity>limit/2 ? limit+1 : capacity*2;
            unsigned char *grown=realloc(raw,next);
            if (!grown) { free(raw); close(fd); pixel_fail(c,"Allocation failed"); }
            raw=grown; capacity=next;
        }
        size_t amount=capacity-used; if (amount>65536) amount=65536;
        ssize_t got=read(fd,raw+used,amount);
        if (got<0) { if (errno==EINTR) continue; result=pixel_io_errno(errno); break; }
        if (!got) break;
        if ((size_t)got>c->io_remaining) { free(raw); close(fd); pixel_fail(c,"File operation budget exceeded"); }
        c->io_remaining-=(size_t)got;
        used+=(size_t)got;
        if (used>limit) { result=pixel_io_error("io.too_large","File exceeds read limit"); break; }
    }
    close(fd);
    if (!result.code) {
        if (text) result=pixel_raw_text(c,raw,used);
        else {
            result.value=pixel_byte_array(c,raw,used,type);
        }
    }
    free(raw); return result;
}
static PixelDecoded pixel_io_write(PixelContext *c, int parent, const char *name, PixelValue value, int text) {
    size_t capacity=text ? value.string->length*4 : value.array->length;
    if (capacity>64000000) return pixel_io_error("io.too_large","Binary file output exceeds limit");
    unsigned char *raw=malloc(capacity ? capacity : 1);
    if (!raw) pixel_fail(c,"Allocation failed");
    size_t length=capacity;
    if (text) length=pixel_raw_encode(value,raw);
    else for (size_t i=0; i<length; i++) raw[i]=(unsigned char)pixel_item(value.array,i).integer;
    if (length==SIZE_MAX) { free(raw); return pixel_io_error("io.invalid_encoding","Invalid Unicode scalar in file output"); }
    if (length>c->io_remaining) { free(raw); pixel_fail(c,"File operation budget exceeded"); }
    c->io_remaining-=length;
    struct stat info;
    if (fstatat(parent,name,&info,AT_SYMLINK_NOFOLLOW)==0) {
        if (!S_ISREG(info.st_mode)) { free(raw); return pixel_io_error("io.invalid_file","File output must replace a regular file"); }
    } else if (errno!=ENOENT) { int error=errno; free(raw); return pixel_io_errno(error); }
    char temporary[96]; int fd=-1;
    for (unsigned attempt=0; attempt<100; attempt++) {
        snprintf(temporary,sizeof(temporary),".pixel-native-%ld-%" PRIu64,(long)getpid(),++c->next_file);
        if (!strcmp(temporary,name)) continue;
        fd=openat(parent,temporary,O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600);
        if (fd>=0 || errno!=EEXIST) break;
    }
    if (fd<0) { int error=errno; free(raw); return pixel_io_errno(error); }
    PixelDecoded result={0}; size_t written=0;
    while (written<length && !pixel_cancelled) {
        size_t chunk=length-written; if (chunk>65536) chunk=65536;
        ssize_t count=write(fd,raw+written,chunk);
        if (count<0) { if (errno==EINTR) continue; result=pixel_io_errno(errno); break; }
        if (!count) { result=pixel_io_error("io.failure","File write made no progress"); break; }
        written+=(size_t)count;
    }
    free(raw);
    if (!result.code && !pixel_cancelled && fsync(fd)<0) result=pixel_io_errno(errno);
    if (close(fd)<0 && !result.code) result=pixel_io_errno(errno);
    if (!result.code && !pixel_cancelled && renameat(parent,temporary,parent,name)<0) result=pixel_io_errno(errno);
    unlinkat(parent,temporary,0);
    if (pixel_cancelled) pixel_fail(c,"Execution cancelled");
    if (!result.code) result.value=pixel_value(PIXEL_BOOL,1);
    return result;
}
/* operation: readText=0, readBytes=1, writeText=2, writeBytes=3. */
static PixelDecoded pixel_file(PixelContext *c, PixelValue path, PixelValue value, int operation, int type) {
    if (operation==3 && !pixel_valid_bytes(c,value))
        return (PixelDecoded){.kind="bytes",.code="bytes.range",.message="Byte values must be integers from 0 to 255"};
    if (operation==1 && (value.integer<0 || value.integer>64000000))
        return (PixelDecoded){.kind="bytes",.code="bytes.limit",.message="Binary read limit must be between 0 and 64000000"};
    if (c->io_remaining<8192) pixel_fail(c,"File operation budget exceeded");
    c->io_remaining-=8192;
    PixelDecoded result={0}; int parent=-1; char buffer[4097],*name=NULL;
    result=pixel_io_parent(c,path,operation>=2,&parent,&name,buffer);
    if (!result.code) {
        result=operation<2 ? pixel_io_read(c,parent,name,operation==0,type,operation==0 ? 4000000 : (size_t)value.integer)
                           : pixel_io_write(c,parent,name,value,operation==2);
        close(parent);
    }
    if (result.code) {
        result.path=path;
        result.operation=operation==0 ? "read" : operation==2 ? "write" : NULL;
    }
    return result;
}
static PixelValue pixel_input(PixelContext *c) {
    if (c->input.kind) return pixel_retain(c->input);
    size_t capacity=65536,used=0; unsigned char *raw=malloc(capacity);
    if (!raw) pixel_fail(c,"Allocation failed");
    for (;;) {
        if (pixel_cancelled) { free(raw); pixel_fail(c,"Execution cancelled"); }
        struct pollfd descriptor={.fd=STDIN_FILENO,.events=POLLIN};
        int ready=poll(&descriptor,1,50);
        if (ready<0) { if (errno==EINTR) continue; free(raw); pixel_fail(c,"Input read failed"); }
        if (!ready) continue;
        if (used==capacity) {
            size_t next=capacity*2; if (next>4000001) next=4000001;
            unsigned char *grown=realloc(raw,next);
            if (!grown) { free(raw); pixel_fail(c,"Allocation failed"); }
            raw=grown; capacity=next;
        }
        ssize_t got=read(STDIN_FILENO,raw+used,capacity-used);
        if (got<0) { if (errno==EINTR) continue; free(raw); pixel_fail(c,"Input read failed"); }
        if (!got) break;
        used+=(size_t)got;
        if (used>4000000) { free(raw); pixel_fail(c,"Input exceeds one million characters"); }
    }
    PixelDecoded result=pixel_raw_text(c,raw,used); free(raw);
    if (result.code) pixel_fail(c,result.message);
    c->input=result.value; return pixel_retain(c->input);
}
static int pixel_io_options(PixelContext *c, int argc, char **argv) {
    c->read_root=-1; c->write_root=-1; c->io_remaining=64000000;
    for (int i=1; i<argc; i++) {
        if (!strcmp(argv[i],"--session")) { c->session=1; continue; }
        if (!strcmp(argv[i],"--report-stats")) { c->report_stats=1; continue; }
        if (!strcmp(argv[i],"--buffered-output")) { c->buffered_output=1; continue; }
        if (!strcmp(argv[i],"--memory-profile")) { c->profile_memory=1; continue; }
        if (!strcmp(argv[i],"--read-root") || !strcmp(argv[i],"--write-root")) {
            int *root=!strcmp(argv[i],"--read-root") ? &c->read_root : &c->write_root;
            if (++i==argc || *root>=0) return 0;
            *root=open(argv[i],O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
            if (*root<0) return 0;
        } else {
            int budget=!strcmp(argv[i],"--io-budget");
            int output=!strcmp(argv[i],"--output-budget");
            if (budget || output || !strcmp(argv[i],"--max-steps")) { if (++i==argc) return 0; }
            else if (i!=1) return 0;
            if (!*argv[i]) return 0;
            for (const char *p=argv[i]; *p; p++) if (*p<'0' || *p>'9') return 0;
            char *end; errno=0; unsigned long long value=strtoull(argv[i],&end,10);
            if (errno || *end) return 0;
            if (budget) { if (value<1 || value>64000000) return 0; c->io_remaining=(size_t)value; }
            else if (output) { if (value<1 || value>64000000) return 0; c->output_remaining=(size_t)value; }
            else c->limit=value;
        }
    }
    return 1;
}
