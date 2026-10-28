/* Schema-directed JSON decoding. Partially decoded values are owned locally;
   recoverable failures unwind them before returning a structured Error. */
enum { PIXEL_DECODE_UNSUPPORTED, PIXEL_DECODE_JSON, PIXEL_DECODE_INT, PIXEL_DECODE_FLOAT,
       PIXEL_DECODE_BOOL, PIXEL_DECODE_STRING, PIXEL_DECODE_ARRAY, PIXEL_DECODE_MAP,
       PIXEL_DECODE_RECORD, PIXEL_DECODE_ENUM, PIXEL_DECODE_OPTION };
typedef struct { PixelString *name; int type; } PixelDecodeField;
typedef struct {
    PixelString *name; int tag; size_t count;
    const PixelDecodeField *fields; PixelString *const *names;
} PixelDecodeVariant;
typedef struct {
    int kind,first,second; size_t count;
    const PixelDecodeField *fields; PixelString *const *names;
    const PixelDecodeVariant *variants; const char *spelling;
} PixelDecodeSchema;
static const PixelDecodeSchema *pixel_decode_schema(int);
typedef struct {
    PixelContext *context; size_t nodes,length; uint32_t path[1024]; PixelDecoded error;
} PixelDecodeState;
static void pixel_decode_path_char(PixelDecodeState *s, uint32_t ch) {
    if (s->length<1024) s->path[s->length++]=ch;
}
static void pixel_decode_path_ascii(PixelDecodeState *s, const char *text) {
    while (*text) pixel_decode_path_char(s,(unsigned char)*text++);
}
static void pixel_decode_path_field(PixelDecodeState *s, PixelString *name) {
    if (pixel_identifier(name)) {
        pixel_decode_path_char(s,'.');
        for (size_t i=0; i<name->length && s->length<1024; i++) pixel_decode_path_char(s,name->data[i]);
        return;
    }
    pixel_decode_path_ascii(s,"[\"");
    for (size_t i=0; i<name->length && s->length<1024; i++) {
        uint32_t ch=name->data[i];
        if (ch=='"' || ch=='\\') { pixel_decode_path_char(s,'\\'); pixel_decode_path_char(s,ch); }
        else if (ch==8 || ch==9 || ch==10 || ch==12 || ch==13) {
            pixel_decode_path_char(s,'\\'); pixel_decode_path_char(s,ch==8 ? 'b' : ch==9 ? 't' : ch==10 ? 'n' : ch==12 ? 'f' : 'r');
        } else if (ch<32) {
            const char *hex="0123456789abcdef"; pixel_decode_path_ascii(s,"\\u00");
            pixel_decode_path_char(s,hex[ch>>4]); pixel_decode_path_char(s,hex[ch&15]);
        } else pixel_decode_path_char(s,ch);
    }
    pixel_decode_path_ascii(s,"\"]");
}
static void pixel_decode_path_index(PixelDecodeState *s, size_t index) {
    char buffer[32]; snprintf(buffer,sizeof(buffer),"[%zu]",index); pixel_decode_path_ascii(s,buffer);
}
static void pixel_decode_capture(PixelDecodeState *s, PixelDecoded error) {
    if (s->error.code) return;
    error.path=pixel_string_new(s->context,s->length);
    memcpy((uint32_t *)error.path.string->data,s->path,s->length*sizeof(uint32_t));
    error.actual_value=pixel_retain(error.actual_value); error.operation="jsonDecode"; s->error=error;
}
static void pixel_decode_error_code(PixelDecodeState *s, const char *code, const char *message) {
    pixel_decode_capture(s,(PixelDecoded){.kind="json",.code=code,.message=message});
}
static int pixel_decode_require(PixelDecodeState *s, PixelValue item, int tag) {
    PixelDecoded error=pixel_json_require(item,tag);
    if (error.code) { pixel_decode_capture(s,error); return 0; }
    return 1;
}
static size_t pixel_decode_find(PixelObject *object, PixelString *name) {
    for (size_t i=0; i<object->length; i+=2)
        if (!pixel_string_compare(object->data[i].string,name)) return i+1;
    return SIZE_MAX;
}
static size_t pixel_decode_find_ascii(PixelObject *object, const char *name) {
    size_t length=strlen(name);
    for (size_t i=0; i<object->length; i+=2) {
        PixelString *key=object->data[i].string;
        if (key->length!=length) continue;
        size_t j=0; while (j<length && key->data[j]==(unsigned char)name[j]) j++;
        if (j==length) return i+1;
    }
    return SIZE_MAX;
}
static PixelValue pixel_decode_convert(PixelDecodeState *s, PixelValue item, int type, unsigned depth) {
    PixelContext *c=s->context; const PixelDecodeSchema *schema=pixel_decode_schema(type);
    if (pixel_cancelled) pixel_fail(c,"Execution cancelled");
    if (++s->nodes>100000 || depth>128) {
        pixel_decode_error_code(s,"json.structure_limit","JSON decode structure budget exceeded"); return (PixelValue){0};
    }
    if (schema->kind==PIXEL_DECODE_JSON) return pixel_retain(item);
    if (schema->kind>=PIXEL_DECODE_INT && schema->kind<=PIXEL_DECODE_STRING) {
        int operation=schema->kind==PIXEL_DECODE_INT ? 5 : schema->kind==PIXEL_DECODE_FLOAT ? 6 : schema->kind==PIXEL_DECODE_BOOL ? 4 : 3;
        PixelDecoded result=pixel_json_access(c,item,(PixelValue){0},operation,0);
        if (result.code) pixel_decode_capture(s,result);
        return result.value;
    }
    if (schema->kind==PIXEL_DECODE_ARRAY) {
        if (!pixel_decode_require(s,item,PIXEL_JSON_ARRAY)) return (PixelValue){0};
        /* Small decoded arrays use the same packed leaves as literals and
           updates. Coordinate pairs otherwise retain full tagged-value slots
           throughout large pixel-source compilation. */
        if (item.array->length<=256) {
            PixelValue children[256]; size_t count=0;
            for (; count<item.array->length; count++) {
                size_t path=s->length; pixel_decode_path_index(s,count);
                children[count]=pixel_decode_convert(s,item.array->data[count],schema->first,depth+1); s->length=path;
                if (s->error.code) {
                    for (size_t i=0; i<count; i++) pixel_release(c,children[i]);
                    return (PixelValue){0};
                }
            }
            PixelValue leaf=pixel_leaf_new(c,children,count);
            for (size_t i=0; i<count; i++) pixel_release(c,children[i]);
            return pixel_array_wrap(c,leaf,count,type);
        }
        PixelValue result=pixel_array_new(c,item.array->length,type);
        for (size_t i=0; i<item.array->length; i++) {
            size_t path=s->length; pixel_decode_path_index(s,i);
            PixelValue child=pixel_decode_convert(s,item.array->data[i],schema->first,depth+1); s->length=path;
            if (s->error.code) { pixel_release(c,result); return (PixelValue){0}; }
            pixel_array_init(result.array,i,child);
        }
        return result;
    }
    if (schema->kind==PIXEL_DECODE_OPTION) {
        if (pixel_decode_schema(schema->first)->kind==PIXEL_DECODE_OPTION) {
            pixel_decode_error_code(s,"json.unsupported_type","Adjacent nested Option types have ambiguous JSON representation"); return (PixelValue){0};
        }
        int empty=item.array->tag==PIXEL_JSON_NULL;
        const PixelDecodeVariant *variant=&schema->variants[empty ? schema->second : 1-schema->second];
        PixelValue result=pixel_enum_new(c,variant->count,type,variant->tag,variant->name,variant->names);
        if (!empty) {
            PixelValue child=pixel_decode_convert(s,item,schema->first,depth+1);
            if (s->error.code) { pixel_release(c,result); return (PixelValue){0}; }
            result.array->data[0]=child;
        }
        return result;
    }
    if (schema->kind==PIXEL_DECODE_MAP) {
        int strings=pixel_decode_schema(schema->first)->kind==PIXEL_DECODE_STRING;
        if (!pixel_decode_require(s,item,strings ? PIXEL_JSON_OBJECT : PIXEL_JSON_ARRAY)) return (PixelValue){0};
        PixelValue result=pixel_map_new(c,type,strings); size_t root_path=s->length;
        for (size_t i=0; i<item.array->length; i+=strings ? 2 : 1) {
            PixelValue key={0},value={0};
            if (strings) {
                key=pixel_retain(item.array->data[i]); pixel_decode_path_field(s,key.string);
                value=pixel_decode_convert(s,item.array->data[i+1],schema->second,depth+1);
            } else {
                pixel_decode_path_index(s,i);
                PixelValue entry=item.array->data[i];
                if (++s->nodes>100000) pixel_decode_error_code(s,"json.structure_limit","JSON decode structure budget exceeded");
                if (!s->error.code && pixel_decode_require(s,entry,PIXEL_JSON_OBJECT)) {
                    size_t key_slot=pixel_decode_find_ascii(entry.array,"key"),value_slot=pixel_decode_find_ascii(entry.array,"value");
                    if (entry.array->length!=4 || key_slot==SIZE_MAX || value_slot==SIZE_MAX)
                        pixel_decode_error_code(s,"json.type_mismatch","Map entry requires exactly key and value");
                    else {
                        size_t entry_path=s->length; pixel_decode_path_ascii(s,".key");
                        key=pixel_decode_convert(s,entry.array->data[key_slot],schema->first,depth+2); s->length=entry_path;
                        if (!s->error.code) {
                            pixel_decode_path_ascii(s,".value");
                            value=pixel_decode_convert(s,entry.array->data[value_slot],schema->second,depth+2);
                        }
                    }
                }
            }
            s->length=root_path;
            if (!s->error.code && pixel_map_find(c,result.array,key)!=SIZE_MAX)
                pixel_decode_capture(s,(PixelDecoded){.kind="collection",.code="collection.duplicate_key",.message="Duplicate map key"});
            if (s->error.code) { pixel_release(c,key); pixel_release(c,value); pixel_release(c,result); return (PixelValue){0}; }
            PixelValue updated=pixel_map_set(c,result,key,value); pixel_release(c,result); result=updated;
        }
        return result;
    }
    if (!pixel_decode_require(s,item,PIXEL_JSON_OBJECT)) return (PixelValue){0};
    if (schema->kind==PIXEL_DECODE_UNSUPPORTED) {
        pixel_decode_error_code(s,"json.unsupported_type","Type cannot be decoded from JSON"); return (PixelValue){0};
    }
    PixelObject *object=item.array; const PixelDecodeField *fields=schema->fields;
    size_t count=schema->count,root_path=s->length;
    const PixelDecodeVariant *variant=NULL;
    if (schema->kind==PIXEL_DECODE_ENUM) {
        size_t variant_slot=pixel_decode_find_ascii(object,"variant"),fields_slot=pixel_decode_find_ascii(object,"fields");
        if (object->length!=4 || variant_slot==SIZE_MAX || fields_slot==SIZE_MAX) {
            pixel_decode_error_code(s,"json.type_mismatch","Enum JSON requires exactly variant and fields"); return (PixelValue){0};
        }
        PixelValue name=object->data[variant_slot],payload=object->data[fields_slot];
        if (!pixel_decode_require(s,name,PIXEL_JSON_STRING)) return (PixelValue){0};
        for (size_t i=0; i<schema->count; i++)
            if (!pixel_string_compare(name.array->data[0].string,schema->variants[i].name)) { variant=&schema->variants[i]; break; }
        if (!variant) {
            pixel_decode_path_ascii(s,".variant");
            pixel_decode_capture(s,(PixelDecoded){.kind="json",.code="json.unknown_variant",.message="Unknown JSON enum variant",.actual_value=name.array->data[0]});
            s->length=root_path; return (PixelValue){0};
        }
        if (!pixel_decode_require(s,payload,PIXEL_JSON_OBJECT)) return (PixelValue){0};
        object=payload.array; fields=variant->fields; count=variant->count;
        int valid=object->length==count*2;
        for (size_t i=0; i<count && valid; i++) valid=pixel_decode_find(object,fields[i].name)!=SIZE_MAX;
        pixel_decode_path_ascii(s,".fields");
        if (!valid) {
            pixel_decode_error_code(s,"json.type_mismatch","Enum payload fields differ from variant schema"); s->length=root_path; return (PixelValue){0};
        }
    } else {
        const PixelDecodeField *missing=NULL; PixelString *extra=NULL;
        for (size_t i=0; i<count; i++)
            if (pixel_decode_find(object,fields[i].name)==SIZE_MAX && pixel_decode_schema(fields[i].type)->kind!=PIXEL_DECODE_OPTION &&
                (!missing || pixel_string_compare(fields[i].name,missing->name)<0)) missing=&fields[i];
        if (missing) {
            pixel_decode_path_field(s,missing->name);
            pixel_decode_capture(s,(PixelDecoded){.kind="json",.code="json.missing_field",.message="Missing required JSON field",
                .expected=pixel_decode_schema(missing->type)->spelling,.actual="missing"}); s->length=root_path; return (PixelValue){0};
        }
        for (size_t i=0; i<object->length; i+=2) {
            PixelString *name=object->data[i].string; int found=0;
            for (size_t j=0; j<count; j++) if (!pixel_string_compare(fields[j].name,name)) { found=1; break; }
            if (!found && (!extra || pixel_string_compare(name,extra)<0)) extra=name;
        }
        if (extra) {
            pixel_decode_path_field(s,extra); pixel_decode_error_code(s,"json.unknown_field","Unknown JSON field");
            s->length=root_path; return (PixelValue){0};
        }
    }
    PixelValue result=variant ? pixel_enum_new(c,count,type,variant->tag,variant->name,variant->names)
                              : pixel_record_new(c,count,type,schema->names);
    for (size_t i=0; i<count; i++) {
        size_t field_path=s->length,slot=pixel_decode_find(object,fields[i].name);
        PixelValue child=slot==SIZE_MAX ? pixel_json_node(c,PIXEL_JSON_NULL,0) : pixel_retain(object->data[slot]);
        pixel_decode_path_field(s,fields[i].name);
        PixelValue decoded=pixel_decode_convert(s,child,fields[i].type,depth+1);
        pixel_release(c,child); s->length=field_path;
        if (s->error.code) { pixel_release(c,result); s->length=root_path; return (PixelValue){0}; }
        result.array->data[i]=decoded;
    }
    s->length=root_path; return result;
}
static PixelDecoded pixel_json_decode(PixelContext *c, PixelValue input, int type) {
    PixelDecodeState state={.context=c,.length=1,.path={'$'}}; PixelValue root;
    if (input.kind==PIXEL_STRING) {
        PixelDecoded parsed=pixel_json_parse(c,input);
        if (parsed.code) { pixel_decode_capture(&state,parsed); return state.error; }
        root=parsed.value;
    } else { pixel_check_type(c,input,PIXEL_JSON); root=pixel_retain(input); }
    PixelValue result=pixel_decode_convert(&state,root,type,0);
    pixel_release(c,root);
    return state.error.code ? state.error : (PixelDecoded){.value=result};
}
