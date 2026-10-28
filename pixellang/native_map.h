/* Persistent insertion-ordered maps. A compressed binary hash trie indexes
   positions in an owned radix-32 vector; hash layout never controls iteration. */
static uint64_t pixel_hash_word(uint64_t hash, uint64_t word) {
    for (unsigned i=0; i<8; i++) { hash^=word&255; hash*=UINT64_C(1099511628211); word>>=8; }
    return hash;
}
static uint64_t pixel_map_hash(PixelContext *c, PixelValue key) {
    PixelValue *pending=NULL; size_t count=0,capacity=0,items=0,bytes=0;
    uint64_t hash=UINT64_C(14695981039346656037);
    for (;;) {
        if (pixel_cancelled) pixel_fail(c,"Execution cancelled");
        if (++items>4096) pixel_fail(c,"Map key item budget exceeded");
        hash=pixel_hash_word(hash,(uint64_t)key.kind);
        bytes+=32;
        if (key.kind==PIXEL_STRING) {
            bytes+=3;
            hash=pixel_hash_word(hash,key.string->length);
            for (size_t i=0; i<key.string->length; i++) {
                uint32_t point=key.string->data[i];
                bytes+=point<128 ? 1 : point<2048 ? 2 : point<65536 ? 3 : 4;
                if (bytes>1000000) pixel_fail(c,"Map key byte budget exceeded");
                hash=pixel_hash_word(hash,point);
            }
        } else if (key.kind==PIXEL_INT || key.kind==PIXEL_BOOL) {
            bytes+=key.kind==PIXEL_INT ? 3 : 4;
            hash=pixel_hash_word(hash,(uint64_t)key.integer);
        } else if (key.kind==PIXEL_FLOAT) {
            bytes+=7; uint64_t bits=0;
            if (key.real!=0.0) memcpy(&bits,&key.real,sizeof(bits));
            hash=pixel_hash_word(hash,bits);
        } else if (key.kind==PIXEL_ENUM) {
            bytes+=pixel_key_type_length(key.array->type);
            for (size_t i=0; i<key.array->variant->length; i++) {
                uint32_t point=key.array->variant->data[i];
                bytes+=point<128 ? 1 : point<2048 ? 2 : point<65536 ? 3 : 4;
            }
            hash=pixel_hash_word(hash,(uint64_t)key.array->type);
            hash=pixel_hash_word(hash,(uint64_t)key.array->tag);
            hash=pixel_hash_word(hash,key.array->length);
            size_t needed=count+key.array->length;
            if (needed>4096-items) pixel_fail(c,"Map key item budget exceeded");
            if (needed>capacity) {
                capacity=needed>capacity*2 ? needed : capacity*2;
                PixelValue *grown=realloc(pending,capacity*sizeof(PixelValue));
                if (!grown) pixel_fail(c,"Allocation failed");
                pending=grown;
            }
            for (size_t i=key.array->length; i>0; i--) pending[count++]=key.array->data[i-1];
        } else pixel_fail(c,"Type does not support map keys");
        if (bytes>1000000) pixel_fail(c,"Map key byte budget exceeded");
        if (!count) break;
        key=pending[--count];
    }
    free(pending); return hash;
}
static int pixel_map_key_equal(PixelContext *c, PixelValue a, PixelValue b) {
    PixelComparison state={0}; int equal=pixel_equal_value(c,a,b,&state); free(state.pairs); return equal;
}
static PixelObject *pixel_map_pair(const PixelObject *map, size_t index) {
    return pixel_item(map->data[1].array,index).array;
}
static size_t pixel_map_next(const PixelObject *map, size_t index) {
    const PixelObject *order=map->data[1].array;
    while (index<order->length && !pixel_item(order,index).kind) index++;
    return index;
}
static size_t pixel_map_find_hash(PixelContext *c, const PixelObject *map, PixelValue key, uint64_t hash) {
    PixelValue node=map->data[0];
    while (node.kind) {
        if (node.array->tag>=0) { node=node.array->data[(hash>>(unsigned)node.array->tag)&1]; continue; }
        if (node.array->hash!=hash) return SIZE_MAX;
        for (size_t i=0; i<node.array->length; i+=2)
            if (pixel_map_key_equal(c,node.array->data[i],key)) return (size_t)node.array->data[i+1].integer;
        return SIZE_MAX;
    }
    return SIZE_MAX;
}
static size_t pixel_map_find(PixelContext *c, const PixelObject *map, PixelValue key) {
    return pixel_map_find_hash(c,map,key,pixel_map_hash(c,key));
}
static PixelValue pixel_map_node(PixelContext *c, size_t length, int tag, uint64_t hash) {
    PixelValue value=pixel_object_new(c,length,0); value.kind=PIXEL_MAP_NODE;
    value.array->tag=tag; value.array->hash=hash; return value;
}
static PixelValue pixel_map_leaf(PixelContext *c, PixelValue key, uint64_t hash, size_t index) {
    PixelValue leaf=pixel_map_node(c,2,-1,hash);
    leaf.array->data[0]=pixel_retain(key); leaf.array->data[1]=pixel_value(PIXEL_INT,(int64_t)index);
    return leaf;
}
static PixelValue pixel_map_insert(PixelContext *c, PixelValue node, PixelValue key, uint64_t hash, size_t index) {
    if (!node.kind) return pixel_map_leaf(c,key,hash,index);
    if (node.array->hash!=hash) {
        uint64_t difference=node.array->hash^hash; unsigned bit=0;
        while (!(difference&1)) { difference>>=1; bit++; }
        if (node.array->tag<0 || bit<(unsigned)node.array->tag) {
            PixelValue branch=pixel_map_node(c,2,(int)bit,node.array->hash);
            size_t slot=(hash>>bit)&1;
            branch.array->data[slot]=pixel_map_leaf(c,key,hash,index);
            branch.array->data[1-slot]=pixel_retain(node); return branch;
        }
    }
    if (node.array->tag<0) {
        PixelValue leaf=pixel_map_node(c,node.array->length+2,-1,hash);
        for (size_t i=0; i<node.array->length; i++) leaf.array->data[i]=pixel_retain(node.array->data[i]);
        leaf.array->data[node.array->length]=pixel_retain(key);
        leaf.array->data[node.array->length+1]=pixel_value(PIXEL_INT,(int64_t)index); return leaf;
    }
    size_t slot=(hash>>(unsigned)node.array->tag)&1;
    PixelValue branch=pixel_map_node(c,2,node.array->tag,node.array->hash);
    branch.array->data[slot]=pixel_map_insert(c,node.array->data[slot],key,hash,index);
    branch.array->data[1-slot]=pixel_retain(node.array->data[1-slot]); return branch;
}
static PixelValue pixel_map_new(PixelContext *c, int type, int string_keys) {
    PixelValue map=pixel_object_new(c,2,type); map.kind=PIXEL_MAP;
    map.array->length=0; map.array->tag=string_keys;
    map.array->data[1]=pixel_array_new(c,0,0); return map;
}
/* Borrow map, consume key/value, preserve the first key and its insertion slot. */
static PixelValue pixel_map_set(PixelContext *c, PixelValue map, PixelValue key, PixelValue value) {
    uint64_t hash=pixel_map_hash(c,key);
    size_t index=pixel_map_find_hash(c,map.array,key,hash); int append=index==SIZE_MAX;
    PixelValue pair=pixel_map_node(c,2,-2,0);
    if (append) { index=map.array->data[1].array->length; pair.array->data[0]=key; }
    else { pair.array->data[0]=pixel_retain(pixel_map_pair(map.array,index)->data[0]); pixel_release(c,key); }
    pair.array->data[1]=value;
    PixelValue root=append ? pixel_map_insert(c,map.array->data[0],pair.array->data[0],hash,index) : pixel_retain(map.array->data[0]);
    PixelValue order=pixel_array_update(c,map.array->data[1],index,pair,append);
    PixelValue result=pixel_object_new(c,2,map.array->type); result.kind=PIXEL_MAP;
    result.array->length=map.array->length+(append ? 1 : 0); result.array->tag=map.array->tag;
    result.array->data[0]=root; result.array->data[1]=order; return result;
}
static PixelValue pixel_map_remove_index(PixelContext *c, PixelValue node, PixelValue key, uint64_t hash) {
    if (node.array->tag<0) {
        size_t slot=0;
        while (slot<node.array->length && !pixel_map_key_equal(c,node.array->data[slot],key)) slot+=2;
        if (slot==node.array->length) pixel_fail(c,"Invalid map removal index");
        if (node.array->length==2) return (PixelValue){0};
        PixelValue leaf=pixel_map_node(c,node.array->length-2,-1,hash);
        for (size_t i=0,j=0; i<node.array->length; i++)
            if (i!=slot && i!=slot+1) leaf.array->data[j++]=pixel_retain(node.array->data[i]);
        return leaf;
    }
    size_t slot=(hash>>(unsigned)node.array->tag)&1;
    PixelValue child=pixel_map_remove_index(c,node.array->data[slot],key,hash);
    if (!child.kind) return pixel_retain(node.array->data[1-slot]);
    PixelValue branch=pixel_map_node(c,2,node.array->tag,node.array->hash);
    branch.array->data[slot]=child; branch.array->data[1-slot]=pixel_retain(node.array->data[1-slot]);
    return branch;
}
static PixelValue pixel_map_remove(PixelContext *c, PixelValue map, PixelValue key) {
    uint64_t hash=pixel_map_hash(c,key); size_t slot=pixel_map_find_hash(c,map.array,key,hash);
    if (slot==SIZE_MAX) return pixel_retain(map);
    if (map.array->length==1) return pixel_map_new(c,map.array->type,map.array->tag);
    PixelValue root=pixel_map_remove_index(c,map.array->data[0],key,hash);
    PixelValue order=pixel_array_update(c,map.array->data[1],slot,(PixelValue){0},0);
    PixelValue result=pixel_object_new(c,2,map.array->type); result.kind=PIXEL_MAP;
    result.array->length=map.array->length-1; result.array->tag=map.array->tag;
    result.array->data[0]=root; result.array->data[1]=order;
    if (order.array->length>32 && order.array->length>2*result.array->length) {
        PixelValue compact=pixel_map_new(c,map.array->type,map.array->tag);
        for (size_t i=pixel_map_next(result.array,0); i<order.array->length; i=pixel_map_next(result.array,i+1)) {
            if (pixel_cancelled) pixel_fail(c,"Execution cancelled");
            PixelObject *pair=pixel_map_pair(result.array,i);
            PixelValue updated=pixel_map_set(c,compact,pixel_retain(pair->data[0]),pixel_retain(pair->data[1]));
            pixel_release(c,compact); compact=updated;
        }
        pixel_release(c,result); result=compact;
    }
    return result;
}
