/* Framed native sessions. Successful transitions retain one typed value;
   execution faults terminate the process rather than publishing partial state. */
static int pixel_session_read(PixelContext *c, unsigned char *data, size_t length, int boundary) {
    size_t used=0;
    while (used<length) {
        if (pixel_cancelled) pixel_fail(c,"Execution cancelled");
        struct pollfd descriptor={.fd=STDIN_FILENO,.events=POLLIN};
        int ready=poll(&descriptor,1,50);
        if (ready<0) { if (errno==EINTR) continue; pixel_fail(c,"Session input failed"); }
        if (!ready) continue;
        ssize_t count=read(STDIN_FILENO,data+used,length-used);
        if (count<0) { if (errno==EINTR) continue; pixel_fail(c,"Session input failed"); }
        if (!count) {
            if (!used && boundary) return 0;
            pixel_fail(c,"Truncated session request");
        }
        used+=(size_t)count;
    }
    return 1;
}
static uint64_t pixel_session_integer(const unsigned char *data, size_t size) {
    uint64_t value=0;
    for (size_t i=0;i<size;i++) value=(value<<8)|data[i];
    return value;
}
static int pixel_session_request(PixelContext *c, uint64_t *identity) {
    unsigned char header[44];
    if (!pixel_session_read(c,header,sizeof(header),1)) return 0;
    if (memcmp(header,"PXQ1",4)) pixel_fail(c,"Invalid session protocol");
    *identity=pixel_session_integer(header+4,8);
    uint64_t steps=pixel_session_integer(header+12,8);
    uint64_t output=pixel_session_integer(header+20,8);
    uint64_t io=pixel_session_integer(header+28,8);
    size_t root_size=(size_t)pixel_session_integer(header+36,4);
    size_t input_size=(size_t)pixel_session_integer(header+40,4);
    if (!*identity || !steps || steps>UINT64_C(3000000000) || !output || output>64000000 ||
        !io || io>64000000 || !root_size || root_size>4096 || input_size>4000000)
        pixel_fail(c,"Invalid session request limits");
    char root[4097];
    pixel_session_read(c,(unsigned char *)root,root_size,0); root[root_size]=0;
    if (memchr(root,0,root_size)) pixel_fail(c,"Invalid session root");
    if (c->read_root>=0) close(c->read_root);
    if (c->write_root>=0) close(c->write_root);
    c->read_root=open(root,O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
    c->write_root=open(root,O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
    if (c->read_root<0 || c->write_root<0) pixel_fail(c,"Cannot open session root");
    c->steps=0; c->limit=steps; c->output_remaining=(size_t)output; c->io_remaining=(size_t)io;
    unsigned char *raw=malloc(input_size ? input_size : 1);
    if (!raw) pixel_fail(c,"Allocation failed");
    pixel_session_read(c,raw,input_size,0);
    PixelDecoded decoded=pixel_raw_text(c,raw,input_size); free(raw);
    if (decoded.code) pixel_fail(c,decoded.message);
    c->input=decoded.value;
    return 1;
}
static void pixel_session_done(PixelContext *c, uint64_t identity) {
    pixel_release(c,c->input); c->input=(PixelValue){0};
    if (c->size || c->frame || c->depth || c->accesses || c->access_bytes)
        pixel_fail(c,"Session transition retained execution state");
    /* JSON values cannot emit a literal record separator at line start. */
    fprintf(stdout,"\036{\"nativeSession\":1,\"id\":%" PRIu64 ",\"steps\":%" PRIu64 "}\n",identity,c->steps);
    if (fflush(stdout)==EOF) pixel_fail(c,"Output write failed");
}
