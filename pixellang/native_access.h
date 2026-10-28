/* Scoped access tokens are identities, never raw pointers exposed as values.
   The active-entry table owns selectors; frames own the referenced bindings. */
static PixelFrame *pixel_access_root(PixelContext *c, uint64_t identity) {
    for (PixelFrame *frame=c->frame; frame; frame=frame->parent)
        if (frame->identity==identity) return frame;
    pixel_fail(c,"inout root is outside its call lifetime");
}
static PixelAccess *pixel_access_entry(PixelContext *c, PixelValue token, int allow_pending_children) {
    if (token.kind!=PIXEL_ACCESS) pixel_fail(c,"Expected an inout access");
    PixelAccess *entry=c->accesses;
    while (entry && entry->identity!=(uint64_t)token.integer) entry=entry->next;
    if (!entry || entry->removed) pixel_fail(c,"Expired inout access");
    pixel_access_root(c,entry->root);
    for (PixelAccess *child=c->accesses; child; child=child->next)
        if (!child->removed && child->parent==entry->identity && (!allow_pending_children || !child->pending))
            pixel_fail(c,"inout access is suspended by a nested borrow");
    return entry;
}
static PixelValue pixel_access_value(PixelContext *c, PixelAccess *entry) {
    PixelValue value=pixel_access_root(c,entry->root)->bindings[entry->slot].value;
    size_t index=0;
    for (size_t i=0; i<entry->count; i++) {
        PixelValue key=entry->fields[i]<0 ? entry->indices[index++] : pixel_value(PIXEL_INT,0);
        size_t slot=pixel_update_slot(c,value,entry->fields[i],key);
        if (value.kind==PIXEL_MAP && slot==SIZE_MAX) pixel_fail(c,"inout map key does not exist");
        value=pixel_selected_child(c,value,slot);
    }
    pixel_check_dynamic(c,value,entry->type);
    return value;
}
static void pixel_check_access(PixelContext *c, PixelValue token, int type) {
    PixelAccess *entry=pixel_access_entry(c,token,1);
    if (entry->type!=type) pixel_fail(c,"inout access type mismatch");
    pixel_access_value(c,entry);
}
static PixelValue pixel_access_read(PixelContext *c, PixelValue token) {
    return pixel_retain(pixel_access_value(c,pixel_access_entry(c,token,1)));
}
static void pixel_access_claim(PixelContext *c, PixelValue token, PixelFrame *frame) {
    PixelAccess *entry=pixel_access_entry(c,token,0);
    if (!entry->pending) pixel_fail(c,"inout arguments require a fresh scoped borrow");
    entry->pending=0; entry->owner=frame->identity;
}
static void pixel_access_direct_write(PixelContext *c, PixelFrame *frame, size_t slot) {
    if (frame->bindings[slot].borrow) pixel_fail(c,"Cannot write a root during an exclusive inout access");
}
static PixelValue pixel_access_begin(PixelContext *c, PixelFrame *frame, size_t slot,
                                    const int *fields, PixelValue *indices, size_t count, size_t index_count, int type) {
    PixelBinding *binding=&frame->bindings[slot];
    if (!binding->value.kind) pixel_fail(c,"inout root is uninitialized");
    if (binding->readonly) pixel_fail(c,"Captured values are readonly");
    PixelAccess *parent=NULL;
    if (binding->value.kind==PIXEL_ACCESS) parent=pixel_access_entry(c,binding->value,0);
    size_t prefix=parent ? parent->count : 0, prefix_indices=parent ? parent->index_count : 0;
    if (prefix+count>128) pixel_fail(c,"inout address path exceeds 128 selectors");
    PixelFrame *root=parent ? pixel_access_root(c,parent->root) : frame;
    size_t root_slot=parent ? parent->slot : slot;
    if (root->bindings[root_slot].borrow!=(parent ? parent->identity : 0))
        pixel_fail(c,"Overlapping inout accesses require exclusive access");
    if (c->next_access==INT64_MAX) pixel_fail(c,"inout identity limit exceeded");
    size_t index_offset=sizeof(PixelAccess)+(prefix+count)*sizeof(int);
    size_t alignment=_Alignof(PixelValue);
    index_offset=(index_offset+alignment-1)/alignment*alignment;
    size_t bytes=index_offset+(prefix_indices+index_count)*sizeof(PixelValue);
    if (bytes>268435456-c->access_bytes) pixel_fail(c,"inout storage budget exceeded");
    PixelAccess *entry=calloc(1,bytes);
    if (!entry) pixel_fail(c,"Allocation failed");
    entry->storage_bytes=bytes;
    entry->fields=(int *)(entry+1);
    entry->indices=(PixelValue *)((unsigned char *)entry+index_offset);
    c->access_bytes+=bytes;
    entry->identity=++c->next_access; entry->owner=frame->identity; entry->root=root->identity;
    entry->slot=root_slot; entry->parent=parent ? parent->identity : 0; entry->parent_entry=parent;
    entry->pending=1; entry->type=type; entry->count=prefix+count; entry->index_count=prefix_indices+index_count;
    for (size_t i=0; i<prefix; i++) entry->fields[i]=parent->fields[i];
    for (size_t i=0; i<count; i++) entry->fields[prefix+i]=fields[i];
    for (size_t i=0; i<prefix_indices; i++) entry->indices[i]=pixel_retain(parent->indices[i]);
    for (size_t i=0; i<index_count; i++) entry->indices[prefix_indices+i]=pixel_retain(indices[i]);
    pixel_access_value(c,entry); /* Validate before publishing the exclusive lock. */
    entry->next=c->accesses;
    if (entry->next) entry->next->previous=entry;
    c->accesses=entry; root->bindings[root_slot].borrow=entry->identity;
    return pixel_value(PIXEL_ACCESS,(int64_t)entry->identity);
}
static void pixel_access_write(PixelContext *c, PixelValue token, PixelValue value) {
    PixelAccess *entry=pixel_access_entry(c,token,0);
    pixel_check_dynamic(c,value,entry->type);
    PixelBinding *binding=&pixel_access_root(c,entry->root)->bindings[entry->slot];
    PixelValue updated=entry->count ? pixel_update_path(c,binding->value,entry->fields,entry->indices,entry->count,1,value) : value;
    pixel_release(c,binding->value); binding->value=updated;
}
static void pixel_access_sweep(PixelContext *c) {
    /* Parents are always older than their children. Visit oldest first so
       removal propagates to every descendant in one pass. Parent pointers
       remain valid until the following newest-first destruction pass. */
    PixelAccess *oldest=c->accesses;
    while (oldest && oldest->next) oldest=oldest->next;
    for (PixelAccess *entry=oldest; entry; entry=entry->previous)
        if (entry->parent_entry && entry->parent_entry->removed) entry->removed=1;
    /* Newest children precede their parents; restore root locks in that order. */
    PixelAccess **link=&c->accesses;
    while (*link) {
        PixelAccess *entry=*link;
        if (!entry->removed) { link=&entry->next; continue; }
        PixelBinding *binding=&pixel_access_root(c,entry->root)->bindings[entry->slot];
        if (binding->borrow==entry->identity) binding->borrow=entry->parent;
        for (size_t i=0; i<entry->index_count; i++) pixel_release(c,entry->indices[i]);
        if (entry->next) entry->next->previous=entry->previous;
        *link=entry->next; c->access_bytes-=entry->storage_bytes; free(entry);
    }
}
static void pixel_access_release_frame(PixelContext *c, PixelFrame *frame) {
    int removed=0;
    for (PixelAccess *entry=c->accesses; entry; entry=entry->next)
        if (entry->owner==frame->identity || entry->root==frame->identity) {
            entry->removed=1; removed=1;
        }
    /* Ordinary helper returns leave enclosing borrows intact. No tombstones
       survive a prior sweep, so there is nothing to propagate or free unless
       this frame actually removed an access. */
    if (removed) pixel_access_sweep(c);
}
static void pixel_access_unwind(PixelContext *c, PixelFrame *frame, size_t boundary) {
    for (size_t i=boundary; i<c->size; i++) if (c->stack[i].kind==PIXEL_ACCESS) {
        PixelValue token=c->stack[i];
        for (size_t j=frame->base; j<boundary; j++)
            if (c->stack[j].kind==PIXEL_ACCESS && c->stack[j].integer==token.integer)
                pixel_fail(c,"Cannot unwind a duplicated inout access");
        PixelAccess *entry=pixel_access_entry(c,token,1);
        if (entry->owner!=frame->identity || !entry->pending) pixel_fail(c,"Cannot discard an active inout parameter");
    }
    for (size_t i=boundary; i<c->size; i++) if (c->stack[i].kind==PIXEL_ACCESS) {
        for (PixelAccess *entry=c->accesses; entry; entry=entry->next)
            if (entry->identity==(uint64_t)c->stack[i].integer) entry->removed=1;
    }
    for (PixelAccess *entry=c->accesses; entry; entry=entry->next)
        if (!entry->removed && entry->parent_entry && entry->parent_entry->removed)
            pixel_fail(c,"Cannot discard an inout access with a retained child");
    pixel_access_sweep(c);
}
