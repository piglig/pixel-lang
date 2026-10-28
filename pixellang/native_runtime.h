/* PixelLang native value runtime. No interpreter or Python dependency. */
#ifdef __APPLE__
#define _DARWIN_C_SOURCE 1
#endif
#ifndef _POSIX_C_SOURCE
#define _POSIX_C_SOURCE 200809L
#endif
#include <stdint.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <signal.h>
#include <errno.h>
#include <limits.h>
#include <math.h>
#include <float.h>
#include <string.h>
#include <zlib.h>

#if DBL_MANT_DIG != 53 || DBL_MAX_EXP != 1024
#error "PixelLang requires IEEE 754 binary64 doubles"
#endif
_Static_assert(sizeof(double) == 8, "PixelLang requires 64-bit doubles");

typedef struct { size_t references, length; const uint32_t *data; } PixelString;
typedef struct PixelObject PixelObject;
typedef struct { int kind; union { int64_t integer; double real; PixelString *string; PixelObject *array; }; } PixelValue;
struct PixelObject {
    size_t references, length, storage_length;
    int type, tag, vector, packed;
    /* Hash belongs to map nodes; variant belongs to enums. They never coexist. */
    union { uint64_t hash; PixelString *variant; };
    /* Field descriptors are no longer read once the last reference is gone. */
    union { PixelString *const *names; PixelObject *release_next; };
    PixelValue data[];
};
enum { PIXEL_UNSET, PIXEL_INT, PIXEL_BOOL, PIXEL_FLOAT, PIXEL_STRING, PIXEL_ARRAY, PIXEL_RECORD, PIXEL_ENUM, PIXEL_CALLABLE, PIXEL_VECTOR, PIXEL_ACCESS, PIXEL_MAP, PIXEL_MAP_NODE, PIXEL_JSON };
static int pixel_type_comparable(int type);
typedef struct { PixelValue value; uint64_t borrow; unsigned char readonly; } PixelBinding;
typedef struct PixelAccess PixelAccess;
struct PixelAccess {
    PixelAccess *next, *previous, *parent_entry;
    uint64_t identity, owner, root, parent;
    size_t slot, count, index_count, storage_bytes;
    int type, pending, removed;
    int *fields;
    PixelValue *indices;
};
typedef struct PixelFrame PixelFrame;
struct PixelFrame {
    PixelFrame *parent;
    uint64_t identity;
    size_t base, count, array_start;
    PixelBinding bindings[];
};
typedef struct {
    PixelValue *stack;
    PixelFrame *frame;
    PixelAccess *accesses;
    size_t size, capacity, string_bytes, array_bytes, array_request, frame_bytes, access_bytes, output_remaining;
    uint64_t steps, limit, next_frame, next_access;
    unsigned depth;
    int function, pc, report_stats, profile_memory, buffered_output, session;
    size_t array_blocks[257], array_bucket_bytes[257];
    int read_root, write_root;
    size_t io_remaining;
    uint64_t next_file;
    PixelValue input;
} PixelContext;
static void pixel_access_release_frame(PixelContext *, PixelFrame *);
static void pixel_check_access(PixelContext *, PixelValue, int);
static PixelObject *pixel_map_pair(const PixelObject *, size_t);
static size_t pixel_map_next(const PixelObject *, size_t);
static size_t pixel_map_find(PixelContext *, const PixelObject *, PixelValue);
static size_t pixel_key_type_length(int);
static PixelValue pixel_json_stringify(PixelContext *, PixelValue);
static size_t pixel_json_length(PixelContext *, PixelValue);
static volatile sig_atomic_t pixel_cancelled;
static void pixel_cancel(int signal_number) { (void)signal_number; pixel_cancelled = 1; }
static _Noreturn void pixel_fail(PixelContext *c, const char *message) {
    /* Messages are runtime-owned literals; source names live in the sidecar. */
    fprintf(stderr, "{\"error\":\"%s\",\"function\":%d,\"pc\":%d,\"steps\":%" PRIu64 ",\"arrayBytes\":%zu,\"arrayRequest\":%zu,\"frameArrayStart\":%zu",
            message, c->function, c->pc, c->steps,c->array_bytes,c->array_request,c->frame ? c->frame->array_start : 0);
    if (c->profile_memory) {
        fputs(",\"allocationBuckets\":{",stderr); int comma=0;
        for (size_t i=0; i<257; i++) if (c->array_blocks[i]) {
            fprintf(stderr,"%s\"%zu\":{\"blocks\":%zu,\"bytes\":%zu}",comma ? "," : "",i,c->array_blocks[i],c->array_bucket_bytes[i]); comma=1;
        }
        fputs("}",stderr);
    }
    fputs("}\n",stderr);
    exit(70);
}
static int pixel_object_kind(int kind) { return kind == PIXEL_ARRAY || kind == PIXEL_RECORD || kind == PIXEL_ENUM || kind == PIXEL_CALLABLE || kind == PIXEL_VECTOR || kind == PIXEL_MAP || kind == PIXEL_MAP_NODE || kind == PIXEL_JSON; }
static PixelValue pixel_value(int kind, int64_t integer) {
    return (PixelValue){.kind = kind, .integer = integer};
}
static PixelValue pixel_float(PixelContext *c, double real) {
    if (!isfinite(real)) pixel_fail(c, "Float64 arithmetic exceeds finite range");
    return (PixelValue){.kind = PIXEL_FLOAT, .real = real};
}
static void pixel_check_type(PixelContext *c, PixelValue v, int kind) {
    if (v.kind != kind) pixel_fail(c, "Operand type mismatch");
}
static void pixel_check_array(PixelContext *c, PixelValue v, int type) {
    pixel_check_type(c,v,PIXEL_ARRAY);
    if (v.array->type != type) pixel_fail(c,"Operand array type mismatch");
}
static void pixel_check_map(PixelContext *c, PixelValue v, int type) {
    pixel_check_type(c,v,PIXEL_MAP);
    if (v.array->type!=type) pixel_fail(c,"Operand map type mismatch");
}
static void pixel_check_record(PixelContext *c, PixelValue v, int type) {
    pixel_check_type(c,v,PIXEL_RECORD);
    if (v.array->type != type) pixel_fail(c,"Operand record type mismatch");
}
static void pixel_check_enum(PixelContext *c, PixelValue v, int type) {
    pixel_check_type(c,v,PIXEL_ENUM);
    if (v.array->type != type) pixel_fail(c,"Operand enum type mismatch");
}
static int64_t pixel_typed(PixelContext *c, PixelValue v, int kind) {
    pixel_check_type(c, v, kind);
    return v.integer;
}
/* Stack entries and local slots own values. Pop transfers; load duplicates.
   Literal strings have immortal storage. Dynamic strings are immutable. */
static PixelValue pixel_retain(PixelValue v) {
    if (v.kind == PIXEL_STRING && v.string->references != SIZE_MAX) v.string->references++;
    if (pixel_object_kind(v.kind)) v.array->references++;
    return v;
}
static void pixel_release_leaf(PixelContext *c, PixelValue v) {
    if (v.kind == PIXEL_STRING && v.string->references != SIZE_MAX && !--v.string->references) {
        c->string_bytes -= sizeof(PixelString) + v.string->length * sizeof(uint32_t);
        free(v.string);
    }
}
static void pixel_release(PixelContext *c, PixelValue v) {
    if (!pixel_object_kind(v.kind)) { pixel_release_leaf(c,v); return; }
    if (--v.array->references) return;
    PixelObject *pending=v.array;
    pending->release_next=NULL;
    while (pending) {
        PixelObject *current=pending; pending=current->release_next;
        for (size_t i=0; !current->packed && i<current->storage_length; i++) {
            PixelValue child=current->data[i];
            if (pixel_object_kind(child.kind)) {
                if (!--child.array->references) {
                    child.array->release_next=pending; pending=child.array;
                }
            } else pixel_release_leaf(c,child);
        }
        size_t bytes=sizeof(PixelObject) + current->storage_length*sizeof(PixelValue);
        c->array_bytes -= bytes;
        if (c->profile_memory) {
            size_t bucket=current->storage_length<256 ? current->storage_length : 256;
            c->array_blocks[bucket]--; c->array_bucket_bytes[bucket]-=bytes;
        }
        free(current);
    }
}
static PixelFrame *pixel_frame_enter(PixelContext *c, size_t count) {
    if (c->depth>=PIXEL_MAX_CALL_DEPTH) pixel_fail(c,"Call stack limit exceeded");
    if (count>(268435456-sizeof(PixelFrame))/sizeof(PixelBinding)) pixel_fail(c,"Call frame storage budget exceeded");
    size_t bytes=sizeof(PixelFrame)+count*sizeof(PixelBinding);
    if (bytes>268435456-c->frame_bytes) pixel_fail(c,"Call frame storage budget exceeded");
    if (c->next_frame==UINT64_MAX) pixel_fail(c,"Call frame identity limit exceeded");
    PixelFrame *frame=calloc(1,bytes);
    if (!frame) pixel_fail(c,"Allocation failed");
    frame->parent=c->frame; frame->identity=++c->next_frame;
    frame->count=count; frame->base=c->size; frame->array_start=c->array_bytes;
    c->frame=frame; c->depth++; c->frame_bytes+=bytes;
    return frame;
}
static void pixel_frame_leave(PixelContext *c, PixelFrame *frame) {
    if (c->frame!=frame || c->size!=frame->base) pixel_fail(c,"Invalid native call frame exit");
    pixel_access_release_frame(c,frame);
    for (size_t i=0; i<frame->count; i++) pixel_release(c,frame->bindings[i].value);
    c->frame=frame->parent; c->depth--;
    c->frame_bytes-=sizeof(PixelFrame)+frame->count*sizeof(PixelBinding);
    free(frame);
}
static PixelValue pixel_object_new(PixelContext *c, size_t length, int type) {
    if (length > (268435456-sizeof(PixelObject))/sizeof(PixelValue))
        pixel_fail(c,"Array storage budget exceeded");
    size_t bytes = sizeof(PixelObject) + length*sizeof(PixelValue);
    c->array_request=bytes;
    if (bytes > 268435456-c->array_bytes) pixel_fail(c,"Array storage budget exceeded");
    PixelObject *array = calloc(1,bytes);
    if (!array) pixel_fail(c,"Allocation failed");
    array->references = 1; array->length = length; array->storage_length = length; array->type = type;
    c->array_bytes += bytes;
    if (c->profile_memory) {
        size_t bucket=length<256 ? length : 256;
        c->array_blocks[bucket]++; c->array_bucket_bytes[bucket]+=bytes;
    }
    return (PixelValue){.kind=PIXEL_ARRAY,.array=array};
}
/* Radix-32 array trees own up to 256 values in each leaf and retain shared children.
   Tree recursion is bounded by the array storage limit, not value nesting. */
static size_t pixel_vector_capacity(unsigned shift) { return shift ? (size_t)32<<shift : 256; }
static PixelValue pixel_vector_new(PixelContext *c, size_t length, unsigned shift) {
    size_t width=(size_t)1<<shift;
    size_t slots=shift ? (length+width-1)/width : length;
    PixelValue node=pixel_object_new(c,slots,0); node.kind=PIXEL_VECTOR; node.array->tag=(int)shift;
    if (shift) for (size_t i=0; i<slots; i++) {
        size_t remaining=length-i*width;
        node.array->data[i]=pixel_vector_new(c,remaining<width ? remaining : width,(shift==8 ? 0 : shift-5));
    }
    return node;
}
static PixelValue pixel_array_wrap(PixelContext *c, PixelValue root, size_t length, int type) {
    if (!root.array->tag && root.array->references==1) {
        root.kind=PIXEL_ARRAY; root.array->type=type; root.array->vector=0;
        return root;
    }
    PixelValue array=pixel_object_new(c,1,type);
    array.array->length=length; array.array->vector=1; array.array->data[0]=root;
    return array;
}
/* Published scalar leaves store byte-range integers in bytes and other ints
   in int64 slots. Promotion copies a leaf; existing snapshots never change. */
static PixelValue pixel_leaf_item(const PixelObject *node, size_t index) {
    if (node->packed==1) return pixel_value(PIXEL_INT,((const unsigned char *)node->data)[index]);
    if (node->packed==8) return pixel_value(PIXEL_INT,((const int64_t *)node->data)[index]);
    return node->data[index];
}
static PixelValue pixel_leaf_new(PixelContext *c, const PixelValue *items, size_t count) {
    int width=1;
    for (size_t i=0; i<count; i++) {
        if (items[i].kind!=PIXEL_INT) { width=0; break; }
        if (items[i].integer<0 || items[i].integer>255) width=8;
    }
    size_t slots=width ? (count*(size_t)width+sizeof(PixelValue)-1)/sizeof(PixelValue) : count;
    PixelValue result=pixel_object_new(c,slots,0); result.kind=PIXEL_VECTOR;
    result.array->length=count; result.array->packed=width;
    for (size_t i=0; i<count; i++) {
        if (width==1) ((unsigned char *)result.array->data)[i]=(unsigned char)items[i].integer;
        else if (width==8) ((int64_t *)result.array->data)[i]=items[i].integer;
        else result.array->data[i]=pixel_retain(items[i]);
    }
    return result;
}
static PixelValue pixel_byte_vector(PixelContext *c, const unsigned char *bytes, size_t length, unsigned shift) {
    if (pixel_cancelled) pixel_fail(c,"Execution cancelled");
    if (!shift) {
        PixelValue items[256];
        for (size_t i=0; i<length; i++) items[i]=pixel_value(PIXEL_INT,bytes[i]);
        return pixel_leaf_new(c,items,length);
    }
    size_t width=(size_t)1<<shift,slots=(length+width-1)/width;
    PixelValue node=pixel_object_new(c,slots,0); node.kind=PIXEL_VECTOR; node.array->tag=(int)shift;
    for (size_t i=0; i<slots; i++) {
        size_t remaining=length-i*width;
        node.array->data[i]=pixel_byte_vector(c,bytes+i*width,remaining<width ? remaining : width,(shift==8 ? 0 : shift-5));
    }
    return node;
}
static PixelValue pixel_byte_array(PixelContext *c, const unsigned char *bytes, size_t length, int type) {
    if (length>64000000) pixel_fail(c,"Byte array length exceeds limit");
    unsigned shift=0;
    while (length>pixel_vector_capacity(shift)) shift=shift ? shift+5 : 8;
    return pixel_array_wrap(c,pixel_byte_vector(c,bytes,length,shift),length,type);
}
static PixelValue pixel_array_new(PixelContext *c, size_t length, int type) {
    if (length>(268435456-sizeof(PixelObject))/sizeof(PixelValue)) pixel_fail(c,"Array storage budget exceeded");
    if (length<=256) return pixel_object_new(c,length,type);
    unsigned shift=0;
    while (length>pixel_vector_capacity(shift)) shift=shift ? shift+5 : 8;
    return pixel_array_wrap(c,pixel_vector_new(c,length,shift),length,type);
}
static PixelValue *pixel_array_slot(PixelObject *array, size_t index) {
    if (!array->vector) return &array->data[index];
    PixelObject *node=array->data[0].array;
    for (unsigned shift=(unsigned)node->tag; shift; shift=(shift==8 ? 0 : shift-5))
        node=node->data[(index>>shift)&(shift ? 31 : 255)].array;
    return &node->data[index&255];
}
static PixelValue pixel_item(const PixelObject *array, size_t index) {
    if (!array->vector) return pixel_leaf_item(array,index);
    const PixelObject *node=array->data[0].array;
    for (unsigned shift=(unsigned)node->tag; shift; shift=(shift==8 ? 0 : shift-5))
        node=node->data[(index>>shift)&(shift ? 31 : 255)].array;
    return pixel_leaf_item(node,index&255);
}
/* Only for unpublished, freshly constructed arrays. Transfers ownership. */
static void pixel_array_init(PixelObject *array, size_t index, PixelValue value) {
    *pixel_array_slot(array,index)=value;
}
static PixelValue pixel_vector_edit(PixelContext *c, PixelObject *node, size_t index, PixelValue value) {
    unsigned shift=(unsigned)node->tag; size_t slot=(index>>shift)&(shift ? 31 : 255);
    size_t length=node->length+(slot==node->length);
    if (!shift) {
        if (node->packed && value.kind==PIXEL_INT) {
            int width=node->packed==8 || value.integer<0 || value.integer>255 ? 8 : 1;
            size_t slots=(length*(size_t)width+sizeof(PixelValue)-1)/sizeof(PixelValue);
            PixelValue result=pixel_object_new(c,slots,0); result.kind=PIXEL_VECTOR;
            result.array->length=length; result.array->packed=width;
            if (width==node->packed) memcpy(result.array->data,node->data,node->length*(size_t)width);
            else for (size_t i=0; i<node->length; i++)
                ((int64_t *)result.array->data)[i]=((const unsigned char *)node->data)[i];
            if (width==1) ((unsigned char *)result.array->data)[slot]=(unsigned char)value.integer;
            else ((int64_t *)result.array->data)[slot]=value.integer;
            return result;
        }
        PixelValue items[256];
        for (size_t i=0; i<length; i++) items[i]=i==slot ? value : pixel_leaf_item(node,i);
        PixelValue result=pixel_leaf_new(c,items,length); pixel_release(c,value); return result;
    }
    PixelValue changed=pixel_object_new(c,length,0); changed.kind=PIXEL_VECTOR; changed.array->tag=(int)shift;
    for (size_t i=0; i<node->length; i++) changed.array->data[i]=pixel_retain(node->data[i]);
    PixelValue child=slot<node->length ? pixel_retain(node->data[slot]) : pixel_vector_new(c,0,(shift==8 ? 0 : shift-5));
    PixelValue updated=pixel_vector_edit(c,child.array,index,value);
    pixel_release(c,child); pixel_release(c,changed.array->data[slot]); changed.array->data[slot]=updated;
    return changed;
}
/* Borrow array, consume replacement, return a new value with shared untouched paths. */
static PixelValue pixel_array_update(PixelContext *c, PixelValue array, size_t index, PixelValue value, int append) {
    pixel_check_type(c,array,PIXEL_ARRAY);
    if (index>array.array->length || (!append && index==array.array->length) || (append && index!=array.array->length))
        pixel_fail(c,"Value update index out of bounds");
    PixelValue root=pixel_retain(array.array->vector ? array.array->data[0] : array);
    unsigned shift=(unsigned)root.array->tag;
    if (append && index==pixel_vector_capacity(shift)) {
        PixelValue parent=pixel_object_new(c,1,0); parent.kind=PIXEL_VECTOR; parent.array->tag=(int)(shift ? shift+5 : 8);
        parent.array->data[0]=root; root=parent;
    }
    PixelValue updated=pixel_vector_edit(c,root.array,index,value); pixel_release(c,root);
    return pixel_array_wrap(c,updated,array.array->length+(append ? 1 : 0),array.array->type);
}
/* Append a chunk fitting in one leaf. Copy each ancestor once per chunk. */
static PixelValue pixel_vector_extend_leaf(PixelContext *c, PixelObject *node, size_t index,
                                          PixelObject *source, size_t offset, size_t count) {
    unsigned shift=(unsigned)node->tag;
    size_t slot=(index>>shift)&(shift ? 31 : 255);
    size_t length=shift ? node->length+(slot==node->length) : node->length+count;
    if (!shift) {
        PixelValue items[256];
        for (size_t i=0; i<node->length; i++) items[i]=pixel_leaf_item(node,i);
        for (size_t i=0; i<count; i++) items[node->length+i]=pixel_item(source,offset+i);
        return pixel_leaf_new(c,items,length);
    }
    PixelValue changed=pixel_object_new(c,length,0); changed.kind=PIXEL_VECTOR; changed.array->tag=(int)shift;
    for (size_t i=0; i<node->length; i++) changed.array->data[i]=pixel_retain(node->data[i]);
    PixelValue child=slot<node->length ? pixel_retain(node->data[slot]) : pixel_vector_new(c,0,(shift==8 ? 0 : shift-5));
    PixelValue updated=pixel_vector_extend_leaf(c,child.array,index,source,offset,count);
    pixel_release(c,child); pixel_release(c,changed.array->data[slot]); changed.array->data[slot]=updated;
    return changed;
}
static PixelValue pixel_array_extend(PixelContext *c, PixelValue array, PixelValue added) {
    pixel_check_type(c,array,PIXEL_ARRAY); pixel_check_array(c,added,array.array->type);
    if (!added.array->length) return pixel_retain(array);
    size_t length=array.array->length;
    PixelValue root=pixel_retain(array.array->vector ? array.array->data[0] : array);
    for (size_t offset=0; offset<added.array->length;) {
        if (pixel_cancelled) pixel_fail(c,"Execution cancelled");
        unsigned shift=(unsigned)root.array->tag;
        if (length==pixel_vector_capacity(shift)) {
            PixelValue parent=pixel_object_new(c,1,0); parent.kind=PIXEL_VECTOR; parent.array->tag=(int)(shift ? shift+5 : 8);
            parent.array->data[0]=root; root=parent;
        }
        size_t count=256-(length&255),remaining=added.array->length-offset;
        if (count>remaining) count=remaining;
        PixelValue updated=pixel_vector_extend_leaf(c,root.array,length,added.array,offset,count);
        pixel_release(c,root); root=updated; length+=count; offset+=count;
    }
    return pixel_array_wrap(c,root,length,array.array->type);
}
static PixelValue pixel_vector_remove_last(PixelContext *c, PixelObject *node) {
    unsigned shift=(unsigned)node->tag; size_t length=node->length;
    if (!shift) {
        PixelValue items[256];
        for (size_t i=0; i+1<length; i++) items[i]=pixel_leaf_item(node,i);
        return pixel_leaf_new(c,items,length-1);
    }
    PixelValue child={0};
    if (shift) {
        child=pixel_vector_remove_last(c,node->data[length-1].array);
        if (!child.array->length) { pixel_release(c,child); child=(PixelValue){0}; length--; }
    } else length--;
    PixelValue changed=pixel_object_new(c,length,0); changed.kind=PIXEL_VECTOR; changed.array->tag=(int)shift;
    for (size_t i=0; i<length; i++) {
        if (shift && child.kind && i==length-1) changed.array->data[i]=child;
        else changed.array->data[i]=pixel_retain(node->data[i]);
    }
    return changed;
}
static PixelValue pixel_array_pop(PixelContext *c, PixelValue array) {
    pixel_check_type(c,array,PIXEL_ARRAY);
    if (!array.array->length) return pixel_retain(array);
    PixelValue root=pixel_vector_remove_last(c,array.array->vector ? array.array->data[0].array : array.array);
    while (root.array->tag && root.array->length==1) {
        PixelValue child=pixel_retain(root.array->data[0]); pixel_release(c,root); root=child;
    }
    return pixel_array_wrap(c,root,array.array->length-1,array.array->type);
}
static PixelValue pixel_record_new(PixelContext *c, size_t length, int type, PixelString *const *names) {
    PixelValue value=pixel_object_new(c,length,type);
    value.kind=PIXEL_RECORD; value.array->names=names;
    return value;
}
static PixelValue pixel_enum_new(PixelContext *c, size_t length, int type, int tag,
                                 PixelString *variant, PixelString *const *names) {
    PixelValue value=pixel_record_new(c,length,type,names);
    value.kind=PIXEL_ENUM; value.array->tag=tag; value.array->variant=variant;
    return value;
}
static PixelValue pixel_callable_new(PixelContext *c, size_t count, int type, int target) {
    PixelValue value=pixel_object_new(c,count,type);
    value.kind=PIXEL_CALLABLE; value.array->tag=target;
    return value;
}
static PixelValue pixel_float_bytes(PixelContext *c, PixelValue value, int type) {
    pixel_check_type(c,value,PIXEL_FLOAT);
    uint64_t bits; memcpy(&bits,&value.real,sizeof(bits));
    PixelValue result=pixel_array_new(c,8,type);
    for (size_t i=0; i<8; i++) pixel_array_init(result.array,i,pixel_value(PIXEL_INT,(bits>>(56-8*i))&255));
    return result;
}
static PixelValue pixel_crc32(PixelContext *c, PixelValue value) {
    pixel_check_type(c,value,PIXEL_ARRAY);
    /* IEEE CRC-32, reflected polynomial, matching the PNG chunk checksum. */
    static const uint32_t table[256]={
        UINT32_C(0x00000000),UINT32_C(0x77073096),UINT32_C(0xee0e612c),UINT32_C(0x990951ba),UINT32_C(0x076dc419),UINT32_C(0x706af48f),UINT32_C(0xe963a535),UINT32_C(0x9e6495a3),
        UINT32_C(0x0edb8832),UINT32_C(0x79dcb8a4),UINT32_C(0xe0d5e91e),UINT32_C(0x97d2d988),UINT32_C(0x09b64c2b),UINT32_C(0x7eb17cbd),UINT32_C(0xe7b82d07),UINT32_C(0x90bf1d91),
        UINT32_C(0x1db71064),UINT32_C(0x6ab020f2),UINT32_C(0xf3b97148),UINT32_C(0x84be41de),UINT32_C(0x1adad47d),UINT32_C(0x6ddde4eb),UINT32_C(0xf4d4b551),UINT32_C(0x83d385c7),
        UINT32_C(0x136c9856),UINT32_C(0x646ba8c0),UINT32_C(0xfd62f97a),UINT32_C(0x8a65c9ec),UINT32_C(0x14015c4f),UINT32_C(0x63066cd9),UINT32_C(0xfa0f3d63),UINT32_C(0x8d080df5),
        UINT32_C(0x3b6e20c8),UINT32_C(0x4c69105e),UINT32_C(0xd56041e4),UINT32_C(0xa2677172),UINT32_C(0x3c03e4d1),UINT32_C(0x4b04d447),UINT32_C(0xd20d85fd),UINT32_C(0xa50ab56b),
        UINT32_C(0x35b5a8fa),UINT32_C(0x42b2986c),UINT32_C(0xdbbbc9d6),UINT32_C(0xacbcf940),UINT32_C(0x32d86ce3),UINT32_C(0x45df5c75),UINT32_C(0xdcd60dcf),UINT32_C(0xabd13d59),
        UINT32_C(0x26d930ac),UINT32_C(0x51de003a),UINT32_C(0xc8d75180),UINT32_C(0xbfd06116),UINT32_C(0x21b4f4b5),UINT32_C(0x56b3c423),UINT32_C(0xcfba9599),UINT32_C(0xb8bda50f),
        UINT32_C(0x2802b89e),UINT32_C(0x5f058808),UINT32_C(0xc60cd9b2),UINT32_C(0xb10be924),UINT32_C(0x2f6f7c87),UINT32_C(0x58684c11),UINT32_C(0xc1611dab),UINT32_C(0xb6662d3d),
        UINT32_C(0x76dc4190),UINT32_C(0x01db7106),UINT32_C(0x98d220bc),UINT32_C(0xefd5102a),UINT32_C(0x71b18589),UINT32_C(0x06b6b51f),UINT32_C(0x9fbfe4a5),UINT32_C(0xe8b8d433),
        UINT32_C(0x7807c9a2),UINT32_C(0x0f00f934),UINT32_C(0x9609a88e),UINT32_C(0xe10e9818),UINT32_C(0x7f6a0dbb),UINT32_C(0x086d3d2d),UINT32_C(0x91646c97),UINT32_C(0xe6635c01),
        UINT32_C(0x6b6b51f4),UINT32_C(0x1c6c6162),UINT32_C(0x856530d8),UINT32_C(0xf262004e),UINT32_C(0x6c0695ed),UINT32_C(0x1b01a57b),UINT32_C(0x8208f4c1),UINT32_C(0xf50fc457),
        UINT32_C(0x65b0d9c6),UINT32_C(0x12b7e950),UINT32_C(0x8bbeb8ea),UINT32_C(0xfcb9887c),UINT32_C(0x62dd1ddf),UINT32_C(0x15da2d49),UINT32_C(0x8cd37cf3),UINT32_C(0xfbd44c65),
        UINT32_C(0x4db26158),UINT32_C(0x3ab551ce),UINT32_C(0xa3bc0074),UINT32_C(0xd4bb30e2),UINT32_C(0x4adfa541),UINT32_C(0x3dd895d7),UINT32_C(0xa4d1c46d),UINT32_C(0xd3d6f4fb),
        UINT32_C(0x4369e96a),UINT32_C(0x346ed9fc),UINT32_C(0xad678846),UINT32_C(0xda60b8d0),UINT32_C(0x44042d73),UINT32_C(0x33031de5),UINT32_C(0xaa0a4c5f),UINT32_C(0xdd0d7cc9),
        UINT32_C(0x5005713c),UINT32_C(0x270241aa),UINT32_C(0xbe0b1010),UINT32_C(0xc90c2086),UINT32_C(0x5768b525),UINT32_C(0x206f85b3),UINT32_C(0xb966d409),UINT32_C(0xce61e49f),
        UINT32_C(0x5edef90e),UINT32_C(0x29d9c998),UINT32_C(0xb0d09822),UINT32_C(0xc7d7a8b4),UINT32_C(0x59b33d17),UINT32_C(0x2eb40d81),UINT32_C(0xb7bd5c3b),UINT32_C(0xc0ba6cad),
        UINT32_C(0xedb88320),UINT32_C(0x9abfb3b6),UINT32_C(0x03b6e20c),UINT32_C(0x74b1d29a),UINT32_C(0xead54739),UINT32_C(0x9dd277af),UINT32_C(0x04db2615),UINT32_C(0x73dc1683),
        UINT32_C(0xe3630b12),UINT32_C(0x94643b84),UINT32_C(0x0d6d6a3e),UINT32_C(0x7a6a5aa8),UINT32_C(0xe40ecf0b),UINT32_C(0x9309ff9d),UINT32_C(0x0a00ae27),UINT32_C(0x7d079eb1),
        UINT32_C(0xf00f9344),UINT32_C(0x8708a3d2),UINT32_C(0x1e01f268),UINT32_C(0x6906c2fe),UINT32_C(0xf762575d),UINT32_C(0x806567cb),UINT32_C(0x196c3671),UINT32_C(0x6e6b06e7),
        UINT32_C(0xfed41b76),UINT32_C(0x89d32be0),UINT32_C(0x10da7a5a),UINT32_C(0x67dd4acc),UINT32_C(0xf9b9df6f),UINT32_C(0x8ebeeff9),UINT32_C(0x17b7be43),UINT32_C(0x60b08ed5),
        UINT32_C(0xd6d6a3e8),UINT32_C(0xa1d1937e),UINT32_C(0x38d8c2c4),UINT32_C(0x4fdff252),UINT32_C(0xd1bb67f1),UINT32_C(0xa6bc5767),UINT32_C(0x3fb506dd),UINT32_C(0x48b2364b),
        UINT32_C(0xd80d2bda),UINT32_C(0xaf0a1b4c),UINT32_C(0x36034af6),UINT32_C(0x41047a60),UINT32_C(0xdf60efc3),UINT32_C(0xa867df55),UINT32_C(0x316e8eef),UINT32_C(0x4669be79),
        UINT32_C(0xcb61b38c),UINT32_C(0xbc66831a),UINT32_C(0x256fd2a0),UINT32_C(0x5268e236),UINT32_C(0xcc0c7795),UINT32_C(0xbb0b4703),UINT32_C(0x220216b9),UINT32_C(0x5505262f),
        UINT32_C(0xc5ba3bbe),UINT32_C(0xb2bd0b28),UINT32_C(0x2bb45a92),UINT32_C(0x5cb36a04),UINT32_C(0xc2d7ffa7),UINT32_C(0xb5d0cf31),UINT32_C(0x2cd99e8b),UINT32_C(0x5bdeae1d),
        UINT32_C(0x9b64c2b0),UINT32_C(0xec63f226),UINT32_C(0x756aa39c),UINT32_C(0x026d930a),UINT32_C(0x9c0906a9),UINT32_C(0xeb0e363f),UINT32_C(0x72076785),UINT32_C(0x05005713),
        UINT32_C(0x95bf4a82),UINT32_C(0xe2b87a14),UINT32_C(0x7bb12bae),UINT32_C(0x0cb61b38),UINT32_C(0x92d28e9b),UINT32_C(0xe5d5be0d),UINT32_C(0x7cdcefb7),UINT32_C(0x0bdbdf21),
        UINT32_C(0x86d3d2d4),UINT32_C(0xf1d4e242),UINT32_C(0x68ddb3f8),UINT32_C(0x1fda836e),UINT32_C(0x81be16cd),UINT32_C(0xf6b9265b),UINT32_C(0x6fb077e1),UINT32_C(0x18b74777),
        UINT32_C(0x88085ae6),UINT32_C(0xff0f6a70),UINT32_C(0x66063bca),UINT32_C(0x11010b5c),UINT32_C(0x8f659eff),UINT32_C(0xf862ae69),UINT32_C(0x616bffd3),UINT32_C(0x166ccf45),
        UINT32_C(0xa00ae278),UINT32_C(0xd70dd2ee),UINT32_C(0x4e048354),UINT32_C(0x3903b3c2),UINT32_C(0xa7672661),UINT32_C(0xd06016f7),UINT32_C(0x4969474d),UINT32_C(0x3e6e77db),
        UINT32_C(0xaed16a4a),UINT32_C(0xd9d65adc),UINT32_C(0x40df0b66),UINT32_C(0x37d83bf0),UINT32_C(0xa9bcae53),UINT32_C(0xdebb9ec5),UINT32_C(0x47b2cf7f),UINT32_C(0x30b5ffe9),
        UINT32_C(0xbdbdf21c),UINT32_C(0xcabac28a),UINT32_C(0x53b39330),UINT32_C(0x24b4a3a6),UINT32_C(0xbad03605),UINT32_C(0xcdd70693),UINT32_C(0x54de5729),UINT32_C(0x23d967bf),
        UINT32_C(0xb3667a2e),UINT32_C(0xc4614ab8),UINT32_C(0x5d681b02),UINT32_C(0x2a6f2b94),UINT32_C(0xb40bbe37),UINT32_C(0xc30c8ea1),UINT32_C(0x5a05df1b),UINT32_C(0x2d02ef8d)
    };
    uint32_t checksum=UINT32_MAX;
    for (size_t i=0; i<value.array->length; i++) {
        if (pixel_cancelled) pixel_fail(c,"Execution cancelled");
        PixelValue item=pixel_item(value.array,i);
        if (item.kind!=PIXEL_INT || item.integer<0 || item.integer>255)
            pixel_fail(c,"Byte values must be integers from 0 to 255");
        checksum=table[(checksum^(uint32_t)item.integer)&255]^(checksum>>8);
    }
    return pixel_value(PIXEL_INT,(int64_t)(checksum^UINT32_MAX));
}
static PixelValue pixel_utf8_encode(PixelContext *c, PixelValue value, int type) {
    pixel_check_type(c,value,PIXEL_STRING);
    size_t length=0;
    for (size_t i=0; i<value.string->length; i++) {
        uint32_t point=value.string->data[i];
        if (point>0x10ffff || (point>=0xd800 && point<=0xdfff))
            pixel_fail(c,"Text contains an invalid Unicode scalar");
        length+=point<0x80 ? 1 : point<0x800 ? 2 : point<0x10000 ? 3 : 4;
    }
    PixelValue result=pixel_array_new(c,length,type); size_t offset=0;
    for (size_t i=0; i<value.string->length; i++) {
        if (pixel_cancelled) pixel_fail(c,"Execution cancelled");
        uint32_t point=value.string->data[i];
        unsigned count=point<0x80 ? 1 : point<0x800 ? 2 : point<0x10000 ? 3 : 4;
        uint32_t first=count==1 ? point : (count==2 ? 0xc0 : count==3 ? 0xe0 : 0xf0)|(point>>(6*(count-1)));
        pixel_array_init(result.array,offset++,pixel_value(PIXEL_INT,first));
        for (unsigned j=count-1; j>0; j--)
            pixel_array_init(result.array,offset++,pixel_value(PIXEL_INT,0x80|((point>>(6*(j-1)))&0x3f)));
    }
    return result;
}
static PixelValue pixel_string_new(PixelContext *c, size_t length) {
    if (length > 1000000) pixel_fail(c, "String size budget exceeded");
    size_t bytes = sizeof(PixelString) + length * sizeof(uint32_t);
    if (bytes > 268435456 - c->string_bytes) pixel_fail(c, "String storage budget exceeded");
    PixelString *s = malloc(bytes);
    if (!s) pixel_fail(c, "Allocation failed");
    s->references = 1; s->length = length; s->data = (uint32_t *)(s + 1);
    c->string_bytes += bytes;
    return (PixelValue){.kind = PIXEL_STRING, .string = s};
}
static int pixel_string_compare(PixelString *a, PixelString *b) {
    size_t length = a->length < b->length ? a->length : b->length;
    for (size_t i = 0; i < length; i++) {
        if (a->data[i] != b->data[i]) return a->data[i] < b->data[i] ? -1 : 1;
    }
    return (a->length > b->length) - (a->length < b->length);
}
typedef struct { PixelValue value; const char *kind, *code, *message, *operation, *expected, *actual; PixelValue path, actual_value; } PixelDecoded;
/* Decimal grammar is checked before libc conversion; locale remains the C
   startup locale in standalone executables. Underflow to zero is permitted. */
static PixelDecoded pixel_parse_number(PixelContext *c, PixelValue text, int real) {
    const uint32_t *data=text.string->data; size_t n=text.string->length,i=0;
    PixelDecoded invalid={.kind="numeric",.code="numeric.invalid_text",
        .message=real ? "Invalid decimal float64" : "Invalid decimal integer"};
    if (!n || (!real && n>21)) return invalid;
    int negative=data[0]=='-';
    if (data[0]=='+' || negative) i++;
    size_t start=i;
    while (i<n && data[i]>='0' && data[i]<='9') i++;
    if (i==start) return invalid;
    if (real && i<n && data[i]=='.') {
        start=++i; while (i<n && data[i]>='0' && data[i]<='9') i++;
        if (i==start) return invalid;
    }
    if (real && i<n && (data[i]=='e' || data[i]=='E')) {
        i++; if (i<n && (data[i]=='+' || data[i]=='-')) i++;
        start=i; while (i<n && data[i]>='0' && data[i]<='9') i++;
        if (i==start) return invalid;
    }
    if (i!=n) return invalid;
    if (pixel_cancelled) pixel_fail(c,"Execution cancelled");
    if (real) {
        char *buffer=malloc(n+1);
        if (!buffer) pixel_fail(c,"Allocation failed");
        for (i=0; i<n; i++) buffer[i]=(char)data[i];
        buffer[n]=0; double value=strtod(buffer,NULL); free(buffer);
        if (!isfinite(value)) return (PixelDecoded){.kind="numeric",.code="numeric.range",.message="Float64 exceeds finite range"};
        return (PixelDecoded){.value=pixel_float(c,value)};
    }
    uint64_t value=0,limit=negative ? UINT64_C(9223372036854775808) : INT64_MAX;
    for (i=(data[0]=='+' || negative); i<n; i++) {
        unsigned digit=data[i]-'0';
        if (value>(limit-digit)/10) return (PixelDecoded){.kind="numeric",.code="numeric.range",.message="Signed 64-bit integer overflow"};
        value=value*10+digit;
    }
    int64_t signed_value=negative ? (value==UINT64_C(9223372036854775808) ? INT64_MIN : -(int64_t)value) : (int64_t)value;
    return (PixelDecoded){.value=pixel_value(PIXEL_INT,signed_value)};
}
static PixelValue pixel_ascii(PixelContext *c, const char *text) {
    size_t length=strlen(text); PixelValue result=pixel_string_new(c,length);
    for (size_t i=0; i<length; i++) ((uint32_t *)result.string->data)[i]=(unsigned char)text[i];
    return result;
}
static int pixel_valid_bytes(PixelContext *c, PixelValue value) {
    pixel_check_type(c,value,PIXEL_ARRAY);
    for (size_t i=0; i<value.array->length; i++) {
        if (pixel_cancelled) pixel_fail(c,"Execution cancelled");
        PixelValue item=pixel_item(value.array,i);
        if (item.kind!=PIXEL_INT || item.integer<0 || item.integer>255) return 0;
    }
    return 1;
}
static PixelDecoded pixel_float_from_bytes(PixelContext *c, PixelValue value) {
    if (!pixel_valid_bytes(c,value))
        return (PixelDecoded){.kind="bytes",.code="bytes.range",.message="Byte values must be integers from 0 to 255"};
    if (value.array->length!=8)
        return (PixelDecoded){.kind="bytes",.code="bytes.length",.message="Float64 requires exactly eight bytes"};
    uint64_t bits=0;
    for (size_t i=0; i<8; i++) bits=(bits<<8)|(uint64_t)pixel_item(value.array,i).integer;
    double real; memcpy(&real,&bits,sizeof(real));
    if (!isfinite(real))
        return (PixelDecoded){.kind="numeric",.code="numeric.range",.message="Float64 exceeds finite range"};
    return (PixelDecoded){.value=pixel_float(c,real)};
}
/* Validate/count first, then allocate exactly once and decode. No partial strings
   escape on data errors; memory budgets and cancellation remain fatal failures. */
static size_t pixel_utf8_scan(PixelContext *c, PixelObject *bytes, uint32_t *output) {
    size_t count=0;
    for (size_t i=0; i<bytes->length;) {
        if (pixel_cancelled) pixel_fail(c,"Execution cancelled");
        uint32_t lead=(uint32_t)pixel_item(bytes,i++).integer, point;
        unsigned extra;
        if (lead<0x80) { point=lead; extra=0; }
        else if (lead>=0xc2 && lead<=0xdf) { point=lead&0x1f; extra=1; }
        else if (lead>=0xe0 && lead<=0xef) { point=lead&0x0f; extra=2; }
        else if (lead>=0xf0 && lead<=0xf4) { point=lead&7; extra=3; }
        else return SIZE_MAX;
        if (extra>bytes->length-i) return SIZE_MAX;
        for (unsigned j=0; j<extra; j++) {
            uint32_t next=(uint32_t)pixel_item(bytes,i++).integer;
            if (next<0x80 || next>0xbf) return SIZE_MAX;
            point=(point<<6)|(next&0x3f);
        }
        if ((extra==1 && point<0x80) || (extra==2 && point<0x800) ||
            (extra==3 && point<0x10000) || point>0x10ffff || (point>=0xd800 && point<=0xdfff))
            return SIZE_MAX;
        if (output) output[count]=point;
        count++;
    }
    return count;
}
static PixelDecoded pixel_utf8_decode(PixelContext *c, PixelValue value) {
    if (!pixel_valid_bytes(c,value))
        return (PixelDecoded){.kind="bytes",.code="bytes.range",.message="Byte values must be integers from 0 to 255"};
    size_t count=pixel_utf8_scan(c,value.array,NULL);
    if (count==SIZE_MAX)
        return (PixelDecoded){.kind="text",.code="text.invalid_utf8",.message="Invalid UTF-8 byte sequence"};
    PixelValue result=pixel_string_new(c,count);
    pixel_utf8_scan(c,value.array,(uint32_t *)result.string->data);
    return (PixelDecoded){.value=result};
}
static PixelDecoded pixel_zlib(PixelContext *c, PixelValue value, int type, int decode, int64_t limit) {
    if (!pixel_valid_bytes(c,value))
        return (PixelDecoded){.kind="bytes",.code="bytes.range",.message="Byte values must be integers from 0 to 255"};
    /* Byte leaves no longer allocate a tagged PixelValue per output byte.
       Physical allocations still share the unchanged aggregate byte budget. */
    const size_t max_items=64000000;
    if (decode && (limit<0 || (uint64_t)limit>max_items))
        return (PixelDecoded){.kind="bytes",.code="bytes.limit",.message="Decompression limit must fit the heap item budget"};
    size_t count=value.array->length;
    unsigned char *input=malloc(count ? count : 1);
    if (!input) pixel_fail(c,"Allocation failed");
    for (size_t i=0; i<count; i++) input[i]=(unsigned char)pixel_item(value.array,i).integer;
    size_t bound=decode ? (size_t)limit+1 : (size_t)compressBound((uLong)count);
    size_t capacity=bound<65536 ? bound : 65536;
    unsigned char *output=malloc(capacity);
    if (!output) pixel_fail(c,"Allocation failed");
    z_stream stream={0};
    int status=decode ? inflateInit(&stream) : deflateInit(&stream,Z_DEFAULT_COMPRESSION);
    if (status!=Z_OK) pixel_fail(c,"Compression initialization failed");
    stream.next_in=input; stream.avail_in=(uInt)count;
    PixelDecoded result={0};
    for (;;) {
        if (pixel_cancelled) pixel_fail(c,"Execution cancelled");
        size_t used=(size_t)stream.total_out;
        if (used==capacity) {
            if (capacity==bound) {
                if (!decode) pixel_fail(c,"Compression output bound exceeded");
                result=(PixelDecoded){.kind="bytes",.code="bytes.limit",.message="Decompressed data exceeds output limit"}; break;
            }
            capacity=capacity>bound/2 ? bound : capacity*2;
            unsigned char *grown=realloc(output,capacity);
            if (!grown) pixel_fail(c,"Allocation failed");
            output=grown;
        }
        size_t available=capacity-used;
        stream.next_out=output+used;
        stream.avail_out=(uInt)(available<65536 ? available : 65536);
        status=decode ? inflate(&stream,Z_NO_FLUSH) : deflate(&stream,Z_FINISH);
        if (status==Z_MEM_ERROR) pixel_fail(c,"Compression allocation failed");
        if (status==Z_DATA_ERROR || status==Z_NEED_DICT) {
            result=(PixelDecoded){.kind="bytes",.code="bytes.compression",.message="Invalid zlib stream"}; break;
        }
        if (decode && stream.total_out>(uint64_t)limit) {
            result=(PixelDecoded){.kind="bytes",.code="bytes.limit",.message="Decompressed data exceeds output limit"}; break;
        }
        if (status==Z_STREAM_END) {
            if (decode && stream.avail_in) result=(PixelDecoded){.kind="bytes",.code="bytes.compression",.message="Truncated or trailing zlib stream"};
            break;
        }
        if (decode && (status==Z_BUF_ERROR || (!stream.avail_in && stream.avail_out))) {
            result=(PixelDecoded){.kind="bytes",.code="bytes.compression",.message="Truncated or trailing zlib stream"}; break;
        }
        if (status!=Z_OK && status!=Z_BUF_ERROR) pixel_fail(c,"Compression runtime failure");
    }
    size_t length=(size_t)stream.total_out;
    if (decode) inflateEnd(&stream); else deflateEnd(&stream);
    free(input);
    if (!result.code) {
        result.value=pixel_byte_array(c,output,length,type);
    }
    free(output); return result;
}
static PixelValue pixel_concat(PixelContext *c, PixelValue a, PixelValue b) {
    pixel_check_type(c,a,PIXEL_STRING); pixel_check_type(c,b,PIXEL_STRING);
    PixelValue result = pixel_string_new(c,a.string->length + b.string->length);
    uint32_t *data = (uint32_t *)result.string->data;
    memcpy(data,a.string->data,a.string->length*sizeof(uint32_t));
    memcpy(data+a.string->length,b.string->data,b.string->length*sizeof(uint32_t));
    return result;
}
static PixelValue pixel_string_slice(PixelContext *c, PixelValue value, int64_t start, int64_t end) {
    pixel_check_type(c,value,PIXEL_STRING);
    if (start < 0 || end < start || (uint64_t)end > value.string->length)
        pixel_fail(c,"Slice bounds out of range");
    PixelValue result = pixel_string_new(c,(size_t)(end-start));
    memcpy((uint32_t *)result.string->data,value.string->data+start,(size_t)(end-start)*sizeof(uint32_t));
    return result;
}
static size_t pixel_length(PixelContext *c, PixelValue value) {
    if (value.kind==PIXEL_JSON) return pixel_json_length(c,value);
    if (value.kind == PIXEL_STRING) return value.string->length;
    if (value.kind == PIXEL_ARRAY) return value.array->length;
    if (value.kind == PIXEL_MAP) return value.array->length;
    pixel_fail(c,"Length requires string or array");
    return 0;
}
static PixelValue pixel_join(PixelContext *c, PixelValue values, PixelValue separator) {
    pixel_check_type(c,values,PIXEL_ARRAY); pixel_check_type(c,separator,PIXEL_STRING);
    size_t length=0, count=values.array->length, width=separator.string->length;
    for (size_t i=0; i<count; i++) {
        PixelValue item=pixel_item(values.array,i); pixel_check_type(c,item,PIXEL_STRING);
        if (item.string->length>1000000-length) pixel_fail(c,"String size budget exceeded");
        length+=item.string->length;
        if (i) {
            if (width>1000000-length) pixel_fail(c,"String size budget exceeded");
            length+=width;
        }
    }
    PixelValue result=pixel_string_new(c,length);
    uint32_t *dest=(uint32_t *)result.string->data; size_t offset=0;
    for (size_t i=0; i<count; i++) {
        if (i) { memcpy(dest+offset,separator.string->data,width*sizeof(uint32_t)); offset+=width; }
        PixelString *item=pixel_item(values.array,i).string;
        memcpy(dest+offset,item->data,item->length*sizeof(uint32_t)); offset+=item->length;
    }
    return result;
}
/* Unicode White_Space plus U+001C..U+001F, matching language trim semantics. */
static int pixel_whitespace(uint32_t point) {
    return (point>=9 && point<=13) || (point>=28 && point<=32) || point==0x85 ||
           point==0xa0 || point==0x1680 || (point>=0x2000 && point<=0x200a) ||
           point==0x2028 || point==0x2029 || point==0x202f || point==0x205f || point==0x3000;
}
static PixelValue pixel_trim(PixelContext *c, PixelValue value) {
    pixel_check_type(c,value,PIXEL_STRING);
    size_t start=0,end=value.string->length;
    while (start<end && pixel_whitespace(value.string->data[start])) start++;
    while (end>start && pixel_whitespace(value.string->data[end-1])) end--;
    if (!start && end==value.string->length) return pixel_retain(value);
    return pixel_string_slice(c,value,(int64_t)start,(int64_t)end);
}
static PixelValue pixel_replace(PixelContext *c, PixelValue value, PixelValue needle, PixelValue replacement) {
    pixel_check_type(c,value,PIXEL_STRING); pixel_check_type(c,needle,PIXEL_STRING);
    pixel_check_type(c,replacement,PIXEL_STRING);
    size_t n=value.string->length,m=needle.string->length,r=replacement.string->length;
    /* KMP records non-overlapping matches in linear time, including repetitive input.
       Temporary storage is bounded by the one-million-codepoint string limit. */
    size_t *prefix=calloc(m ? m : 1,sizeof(size_t));
    size_t *matches=malloc((n+1)*sizeof(size_t));
    if (!prefix || !matches) pixel_fail(c,"Allocation failed");
    size_t count=0;
    if (!m) {
        for (size_t i=0; i<=n; i++) matches[count++]=i;
    } else {
        for (size_t i=1,j=0; i<m; i++) {
            while (j && needle.string->data[i]!=needle.string->data[j]) j=prefix[j-1];
            if (needle.string->data[i]==needle.string->data[j]) j++;
            prefix[i]=j;
        }
        for (size_t i=0,j=0; i<n; i++) {
            if (pixel_cancelled) pixel_fail(c,"Execution cancelled");
            while (j && value.string->data[i]!=needle.string->data[j]) j=prefix[j-1];
            if (value.string->data[i]==needle.string->data[j]) j++;
            if (j==m) { matches[count++]=i+1-m; j=0; }
        }
    }
    free(prefix);
    if (!count) { free(matches); return pixel_retain(value); }
    size_t length=n-count*m;
    if (r && count>(1000000-length)/r) pixel_fail(c,"String size budget exceeded");
    length+=count*r;
    PixelValue result=pixel_string_new(c,length);
    uint32_t *dest=(uint32_t *)result.string->data; size_t source=0,offset=0;
    for (size_t i=0; i<count; i++) {
        size_t span=matches[i]-source;
        memcpy(dest+offset,value.string->data+source,span*sizeof(uint32_t)); offset+=span;
        memcpy(dest+offset,replacement.string->data,r*sizeof(uint32_t)); offset+=r;
        source=matches[i]+m;
    }
    memcpy(dest+offset,value.string->data+source,(n-source)*sizeof(uint32_t));
    free(matches); return result;
}
static PixelValue pixel_slice(PixelContext *c, PixelValue value, int64_t start, int64_t end) {
    if (value.kind == PIXEL_STRING) return pixel_string_slice(c,value,start,end);
    pixel_check_type(c,value,PIXEL_ARRAY);
    if (start < 0 || end < start || (uint64_t)end > value.array->length)
        pixel_fail(c,"Slice bounds out of range");
    PixelValue result = pixel_array_new(c,(size_t)(end-start),value.array->type);
    for (size_t i=0; i<result.array->length; i++) pixel_array_init(result.array,i,pixel_retain(pixel_item(value.array,start+i)));
    return result;
}
static PixelValue pixel_index(PixelContext *c, PixelValue value, int64_t index) {
    if (index < 0 || (uint64_t)index >= pixel_length(c,value)) pixel_fail(c,"Index out of bounds");
    return value.kind == PIXEL_STRING ? pixel_string_slice(c,value,index,index+1) : pixel_retain(pixel_item(value.array,index));
}
static void pixel_tick(PixelContext *c, int function, int pc) {
    c->function = function; c->pc = pc;
    if (pixel_cancelled) pixel_fail(c, "Execution cancelled");
    if (c->steps >= c->limit) pixel_fail(c, "Instruction budget exceeded");
    c->steps++;
}
static void pixel_push(PixelContext *c, PixelValue value) {
    if (c->size == c->capacity) {
        if (c->capacity >= 1048576) pixel_fail(c, "Operand stack limit exceeded");
        size_t capacity = c->capacity ? c->capacity * 2 : 64;
        PixelValue *stack = realloc(c->stack, capacity * sizeof(*stack));
        if (!stack) pixel_fail(c, "Allocation failed");
        c->stack = stack; c->capacity = capacity;
    }
    c->stack[c->size++] = value;
}
static PixelValue pixel_pop(PixelContext *c, size_t base) {
    if (c->size <= base) pixel_fail(c, "Operand stack underflow");
    return c->stack[--c->size];
}
static void pixel_iteration(PixelContext *c, PixelValue value, int paired, int strings_type, int indices_type) {
    size_t length = pixel_length(c,value);
    /* Immutable arrays already are snapshots; transfer the input ownership. */
    PixelValue values = value;
    if (value.kind == PIXEL_STRING) {
        values = pixel_array_new(c,length,strings_type);
        for (size_t i=0; i<length; i++) {
            if (pixel_cancelled) pixel_fail(c,"Execution cancelled");
            pixel_array_init(values.array,i,pixel_string_slice(c,value,(int64_t)i,(int64_t)i+1));
        }
        pixel_release(c,value);
    }
    if (paired) {
        PixelValue indices = pixel_array_new(c,length,indices_type);
        for (size_t i=0; i<length; i++) {
            if (pixel_cancelled) pixel_fail(c,"Execution cancelled");
            pixel_array_init(indices.array,i,pixel_value(PIXEL_INT,(int64_t)i));
        }
        pixel_push(c,indices);
    }
    pixel_push(c,values);
}
typedef struct { const PixelObject *left, *right; } PixelComparedPair;
typedef struct { PixelComparedPair *pairs; size_t capacity, count, work; } PixelComparison;
static size_t pixel_pair_hash(const PixelObject *a, const PixelObject *b) {
    return ((uintptr_t)a >> 4) * (size_t)2654435761u ^ ((uintptr_t)b >> 4);
}
static int pixel_pair_seen(PixelContext *c, PixelComparison *state, const PixelObject *a, const PixelObject *b) {
    if (!state->capacity || state->count*2 >= state->capacity) {
        size_t capacity = state->capacity ? state->capacity*2 : 16;
        PixelComparedPair *pairs = calloc(capacity,sizeof(*pairs));
        if (!pairs) pixel_fail(c,"Allocation failed");
        for (size_t i=0; i<state->capacity; i++) if (state->pairs[i].left) {
            PixelComparedPair pair = state->pairs[i];
            size_t slot = pixel_pair_hash(pair.left,pair.right)&(capacity-1);
            while (pairs[slot].left) slot=(slot+1)&(capacity-1);
            pairs[slot]=pair;
        }
        free(state->pairs); state->pairs=pairs; state->capacity=capacity;
    }
    size_t slot=pixel_pair_hash(a,b)&(state->capacity-1);
    while (state->pairs[slot].left) {
        if (state->pairs[slot].left==a && state->pairs[slot].right==b) return 1;
        slot=(slot+1)&(state->capacity-1);
    }
    state->pairs[slot]=(PixelComparedPair){a,b}; state->count++;
    return 0;
}
static int pixel_equal_value(PixelContext *c, PixelValue a, PixelValue b, PixelComparison *state) {
    typedef struct { const PixelObject *a, *b; size_t index; int map; } Cursor;
    Cursor *stack=NULL; size_t depth=0, capacity=0;
    for (;;) {
        if (++state->work > 1000000) pixel_fail(c,"Equality comparison budget exceeded");
        if (pixel_cancelled) pixel_fail(c,"Execution cancelled");
        if (a.kind==PIXEL_JSON || b.kind==PIXEL_JSON) pixel_fail(c,"Type does not support value equality");
        if (a.kind != b.kind) pixel_fail(c,"Equality operands have different types");
        if (a.kind == PIXEL_FLOAT) { if (a.real!=b.real) goto different; }
        else if (a.kind == PIXEL_STRING) { if (pixel_string_compare(a.string,b.string)) goto different; }
        else if (pixel_object_kind(a.kind)) {
            if (a.array->type != b.array->type) pixel_fail(c,"Equality operands have different types");
            if (!pixel_type_comparable(a.array->type)) pixel_fail(c,"Type does not support value equality");
            if (a.array != b.array && !pixel_pair_seen(c,state,a.array,b.array)) {
                if (a.kind == PIXEL_ENUM && a.array->tag != b.array->tag) goto different;
                if (a.array->length != b.array->length) goto different;
                if (a.kind == PIXEL_RECORD || a.kind == PIXEL_ENUM) {
                    state->work+=a.array->length + (a.kind==PIXEL_ENUM ? 2 : 0);
                    if (state->work>1000000) pixel_fail(c,"Equality comparison budget exceeded");
                }
                if (a.array->length) {
                    if (depth==capacity) {
                        capacity=capacity ? capacity*2 : 16;
                        Cursor *grown=realloc(stack,capacity*sizeof(*stack));
                        if (!grown) pixel_fail(c,"Allocation failed");
                        stack=grown;
                    }
                    stack[depth++]=(Cursor){a.array,b.array,1,a.kind==PIXEL_MAP};
                    if (a.kind==PIXEL_MAP) {
                        size_t first=pixel_map_next(a.array,0); stack[depth-1].index=first+1;
                        PixelObject *pair=pixel_map_pair(a.array,first);
                        size_t slot=pixel_map_find(c,b.array,pair->data[0]);
                        if (slot==SIZE_MAX) goto different;
                        a=pair->data[1]; b=pixel_map_pair(b.array,slot)->data[1];
                    } else { a=pixel_item(a.array,0); b=pixel_item(b.array,0); }
                    continue;
                }
            }
        } else if (a.integer!=b.integer) goto different;
        while (depth) {
            Cursor *top=&stack[depth-1];
            size_t end=top->map ? top->a->data[1].array->length : top->a->length;
            if (top->map) top->index=pixel_map_next(top->a,top->index);
            if (top->index<end) break;
            depth--;
        }
        if (!depth) { free(stack); return 1; }
        Cursor *cursor=&stack[depth-1];
        if (cursor->map) {
            PixelObject *pair=pixel_map_pair(cursor->a,cursor->index);
            size_t slot=pixel_map_find(c,cursor->b,pair->data[0]);
            if (slot==SIZE_MAX) goto different;
            a=pair->data[1]; b=pixel_map_pair(cursor->b,slot)->data[1];
        } else { a=pixel_item(cursor->a,cursor->index); b=pixel_item(cursor->b,cursor->index); }
        cursor->index++;
    }
different:
    free(stack); return 0;
}
static PixelValue pixel_binary(PixelContext *c, PixelValue a, PixelValue b, int op) {
    if (op == 5 || op == 6) {
        PixelComparison comparison={0};
        int equal=pixel_equal_value(c,a,b,&comparison);
        free(comparison.pairs);
        return pixel_value(PIXEL_BOOL, op == 5 ? equal : !equal);
    }
    if (a.kind == PIXEL_STRING && op >= 7) {
        pixel_check_type(c,b,PIXEL_STRING);
        int comparison = pixel_string_compare(a.string,b.string);
        return pixel_value(PIXEL_BOOL, op == 7 ? comparison < 0 : op == 8 ? comparison <= 0 :
                                      op == 9 ? comparison > 0 : comparison >= 0);
    }
    if (a.kind == PIXEL_FLOAT) {
        pixel_check_type(c, b, PIXEL_FLOAT);
        double x = a.real, y = b.real;
        if ((op == 3 || op == 4) && y == 0.0) pixel_fail(c, "Division by zero");
        switch (op) {
        case 0: return pixel_float(c, x + y);
        case 1: return pixel_float(c, x - y);
        case 2: return pixel_float(c, x * y);
        case 3: return pixel_float(c, x / y);
        case 4: return pixel_float(c, fmod(x, y));
        case 7: return pixel_value(PIXEL_BOOL, x < y);
        case 8: return pixel_value(PIXEL_BOOL, x <= y);
        case 9: return pixel_value(PIXEL_BOOL, x > y);
        case 10: return pixel_value(PIXEL_BOOL, x >= y);
        }
    }
    pixel_typed(c, a, PIXEL_INT); pixel_typed(c, b, PIXEL_INT);
    int64_t x = a.integer, y = b.integer, result = 0;
    switch (op) {
    case 0: if (__builtin_add_overflow(x,y,&result)) goto overflow; break;
    case 1: if (__builtin_sub_overflow(x,y,&result)) goto overflow; break;
    case 2: if (__builtin_mul_overflow(x,y,&result)) goto overflow; break;
    case 3: case 4:
        if (!y) pixel_fail(c, "Division by zero");
        if (x == INT64_MIN && y == -1) {
            if (op == 3) goto overflow;
            result = 0;
        } else result = op == 3 ? x / y : x % y;
        break;
    case 7: return pixel_value(PIXEL_BOOL, x < y);
    case 8: return pixel_value(PIXEL_BOOL, x <= y);
    case 9: return pixel_value(PIXEL_BOOL, x > y);
    case 10: return pixel_value(PIXEL_BOOL, x >= y);
    }
    return pixel_value(PIXEL_INT, result);
overflow:
    pixel_fail(c, "Signed 64-bit integer overflow");
    return pixel_value(PIXEL_INT, 0);
}
static void pixel_output_check(PixelContext *c, PixelValue value, unsigned depth) {
    if (value.kind==PIXEL_JSON) {
        PixelValue text=pixel_json_stringify(c,value); pixel_output_check(c,text,depth); pixel_release(c,text); return;
    }
    if (value.kind==PIXEL_ACCESS) pixel_fail(c,"inout access cannot be serialized as a value");
    if (pixel_cancelled) pixel_fail(c,"Execution cancelled");
    if (value.kind==PIXEL_CALLABLE) pixel_fail(c,"Native callable serialization is not implemented");
    size_t cost = value.kind == PIXEL_STRING ? value.string->length : 20;
    if (cost > c->output_remaining) pixel_fail(c,"Output budget exceeded");
    c->output_remaining -= cost;
    if (pixel_object_kind(value.kind)) {
        if (depth >= 128) pixel_fail(c,"Printed value nesting exceeds 128");
        if (value.kind==PIXEL_MAP) {
            for (size_t i=pixel_map_next(value.array,0); i<value.array->data[1].array->length; i=pixel_map_next(value.array,i+1)) {
                PixelObject *pair=pixel_map_pair(value.array,i);
                pixel_output_check(c,pair->data[0],depth+1); pixel_output_check(c,pair->data[1],depth+1);
            }
            return;
        }
        for (size_t i=0; i<value.array->length; i++) {
            if (value.kind == PIXEL_RECORD)
                pixel_output_check(c,(PixelValue){.kind=PIXEL_STRING,.string=value.array->names[i]},depth+1);
            pixel_output_check(c,pixel_item(value.array,i),depth+1);
        }
    }
}
static void pixel_write(PixelContext *c, PixelValue value) {
    if (value.kind==PIXEL_JSON) {
        PixelValue text=pixel_json_stringify(c,value); pixel_write(c,text); pixel_release(c,text); return;
    }
    if (value.kind == PIXEL_BOOL) fputs(value.integer ? "true" : "false",stdout);
    else if (value.kind == PIXEL_INT) printf("%" PRId64, value.integer);
    else if (value.kind == PIXEL_FLOAT) {
        char number[32];
        snprintf(number, sizeof(number), "%.17g", value.real);
        fputs(number, stdout);
        /* Preserve float identity and negative zero in JSON output. */
        fputs(strpbrk(number, ".eE") ? "" : ".0",stdout);
    }
    else if (value.kind == PIXEL_STRING) {
        putchar('"');
        for (size_t i = 0; i < value.string->length; i++) {
            uint32_t ch = value.string->data[i];
            if (ch == '"' || ch == '\\') { putchar('\\'); putchar((int)ch); }
            else if (ch >= 32 && ch < 127) putchar((int)ch);
            else if (ch <= 65535) printf("\\u%04x",(unsigned)ch);
            else {
                ch -= 65536;
                printf("\\u%04x\\u%04x",(unsigned)(55296+(ch>>10)),(unsigned)(56320+(ch&1023)));
            }
        }
        putchar('"');
    }
    else if (value.kind==PIXEL_MAP) {
        putchar(value.array->tag ? '{' : '[');
        size_t emitted=0;
        for (size_t i=pixel_map_next(value.array,0); i<value.array->data[1].array->length; i=pixel_map_next(value.array,i+1)) {
            if (emitted++) putchar(',');
            PixelObject *pair=pixel_map_pair(value.array,i);
            if (!value.array->tag) fputs("{\"key\":",stdout);
            pixel_write(c,pair->data[0]); fputs(value.array->tag ? ":" : ",\"value\":",stdout);
            pixel_write(c,pair->data[1]); if (!value.array->tag) putchar('}');
        }
        putchar(value.array->tag ? '}' : ']');
    }
    else if (pixel_object_kind(value.kind)) {
        if (value.kind==PIXEL_ENUM) {
            fputs("{\"variant\":",stdout);
            pixel_write(c,(PixelValue){.kind=PIXEL_STRING,.string=value.array->variant});
            fputs(",\"fields\":{",stdout);
        } else putchar(value.kind==PIXEL_RECORD ? '{' : '[');
        for (size_t i=0; i<value.array->length; i++) {
            if (i) putchar(',');
            if (value.kind==PIXEL_RECORD || value.kind==PIXEL_ENUM) {
                pixel_write(c,(PixelValue){.kind=PIXEL_STRING,.string=value.array->names[i]}); putchar(':');
            }
            pixel_write(c,pixel_item(value.array,i));
        }
        if (value.kind==PIXEL_ENUM) fputs("}}",stdout);
        else putchar(value.kind==PIXEL_RECORD ? '}' : ']');
    }
    else pixel_fail(c, "Cannot print uninitialized value");
}
static void pixel_print(PixelContext *c, PixelValue value) {
    pixel_output_check(c,value,0);
    pixel_write(c,value); putchar('\n');
    if (ferror(stdout) || (!c->buffered_output && fflush(stdout) == EOF))
        pixel_fail(c, "Output write failed");
}

static PixelValue pixel_split(PixelContext *c, PixelValue text, PixelValue separator, int type) {
    pixel_check_type(c,text,PIXEL_STRING); pixel_check_type(c,separator,PIXEL_STRING);
    size_t n=text.string->length,m=separator.string->length;
    if (!m) pixel_fail(c,"split separator cannot be empty");
    size_t *prefix=calloc(m,sizeof(size_t)),*matches=malloc((n/m+1)*sizeof(size_t));
    if (!prefix || !matches) pixel_fail(c,"Allocation failed");
    for (size_t i=1,j=0; i<m; i++) {
        while (j && separator.string->data[i]!=separator.string->data[j]) j=prefix[j-1];
        if (separator.string->data[i]==separator.string->data[j]) j++;
        prefix[i]=j;
    }
    size_t count=0;
    for (size_t i=0,j=0; i<n; i++) {
        if (pixel_cancelled) pixel_fail(c,"Execution cancelled");
        while (j && text.string->data[i]!=separator.string->data[j]) j=prefix[j-1];
        if (text.string->data[i]==separator.string->data[j]) j++;
        if (j==m) { matches[count++]=i+1-m; j=0; }
    }
    free(prefix);
    PixelValue result=pixel_array_new(c,count+1,type);
    if (!count) pixel_array_init(result.array,0,pixel_retain(text));
    else {
        size_t start=0;
        for (size_t i=0; i<count; i++) {
            if (pixel_cancelled) pixel_fail(c,"Execution cancelled");
            pixel_array_init(result.array,i,pixel_string_slice(c,text,(int64_t)start,(int64_t)matches[i]));
            start=matches[i]+m;
        }
        pixel_array_init(result.array,count,pixel_string_slice(c,text,(int64_t)start,(int64_t)n));
    }
    free(matches); return result;
}
static PixelValue pixel_array_repeat(PixelContext *c, PixelValue values, int64_t times) {
    pixel_check_type(c,values,PIXEL_ARRAY);
    if (times<0) pixel_fail(c,"Array repeat count must be nonnegative");
    size_t length=values.array->length;
    if (!length || times==1) return pixel_retain(values);
    if ((uint64_t)times>(268435456-sizeof(PixelObject))/sizeof(PixelValue)/length)
        pixel_fail(c,"Array storage budget exceeded");
    size_t total=length*(size_t)times;
    PixelValue result=pixel_array_new(c,total,values.array->type);
    for (size_t i=0; i<total; i++) {
        if (pixel_cancelled) pixel_fail(c,"Execution cancelled");
        pixel_array_init(result.array,i,pixel_retain(pixel_item(values.array,i%length)));
    }
    return result;
}
