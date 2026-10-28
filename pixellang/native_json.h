/* Owned immutable JSON nodes. Numbers retain decimal spelling, rather than
   silently rounding through binary64. Objects keep ordered key/value pairs. */
enum { PIXEL_JSON_NULL, PIXEL_JSON_BOOL, PIXEL_JSON_NUMBER, PIXEL_JSON_STRING,
       PIXEL_JSON_ARRAY, PIXEL_JSON_OBJECT };
static int pixel_json_option_type(int);
static const char *pixel_json_kind_name(int tag) {
    static const char *names[]={"null","bool","number","string","array","object"};
    return tag>=0 && tag<6 ? names[tag] : "invalid";
}
static PixelValue pixel_json_node(PixelContext *c, int tag, size_t count) {
    PixelValue value=pixel_object_new(c,count,-1000); value.kind=PIXEL_JSON;
    value.array->tag=tag; return value;
}
static PixelValue pixel_json_scalar(PixelContext *c, int tag, PixelValue payload) {
    PixelValue result=pixel_json_node(c,tag,1); result.array->data[0]=payload; return result;
}
/* Find the shortest significant decimal that round-trips, then normalize the
   fixed/exponent presentation independently of the precision used by %g. */
static PixelValue pixel_decimal_spelling(PixelContext *c, PixelValue value) {
    char number[64];
    if (value.kind==PIXEL_INT) { snprintf(number,sizeof(number),"%" PRId64,value.integer); return pixel_ascii(c,number); }
    pixel_check_type(c,value,PIXEL_FLOAT);
    if (value.real==0) return pixel_ascii(c,signbit(value.real) ? "-0.0" : "0.0");
    for (int precision=1; precision<=17; precision++) {
        snprintf(number,sizeof(number),"%.*g",precision,value.real);
        double parsed=strtod(number,NULL);
        if (!memcmp(&parsed,&value.real,sizeof(parsed))) break;
    }
    char digits[24],result[64]; int count=0,decimal=0,exponent=0,negative=number[0]=='-',seen_dot=0;
    const char *p=number+negative;
    while (*p && *p!='e' && *p!='E') {
        if (*p=='.') seen_dot=1;
        else { digits[count++]=*p; if (!seen_dot) decimal++; }
        p++;
    }
    if (*p) exponent=(int)strtol(p+1,NULL,10);
    exponent+=decimal-1;
    /* Fixed %g forms below one have leading zeros, which are not significant. */
    int leading=0; while (leading<count-1 && digits[leading]=='0') { leading++; exponent--; }
    if (leading) { memmove(digits,digits+leading,(size_t)(count-leading)); count-=leading; }
    while (count>1 && digits[count-1]=='0') count--;
    size_t used=0; if (negative) result[used++]='-';
    if (exponent<-4 || exponent>=16) {
        result[used++]=digits[0];
        if (count>1) { result[used++]='.'; memcpy(result+used,digits+1,(size_t)count-1); used+=(size_t)count-1; }
        snprintf(result+used,sizeof(result)-used,"e%c%02d",exponent<0 ? '-' : '+',exponent<0 ? -exponent : exponent);
    } else if (exponent<0) {
        result[used++]='0'; result[used++]='.';
        for (int i=-1; i>exponent; i--) result[used++]='0';
        memcpy(result+used,digits,(size_t)count); used+=(size_t)count; result[used]=0;
    } else {
        int before=exponent+1;
        for (int i=0; i<before; i++) result[used++]=i<count ? digits[i] : '0';
        result[used++]='.';
        if (count>before) { memcpy(result+used,digits+before,(size_t)(count-before)); used+=(size_t)(count-before); }
        else result[used++]='0';
        result[used]=0;
    }
    return pixel_ascii(c,result);
}
typedef struct { uint32_t *output; size_t length, nodes; } PixelJsonWriter;
static void pixel_json_char(PixelContext *c, PixelJsonWriter *writer, uint32_t ch) {
    if (writer->length==1000000) pixel_fail(c,"JSON output exceeds one million characters");
    if (writer->output) writer->output[writer->length]=ch;
    writer->length++;
}
static void pixel_json_ascii(PixelContext *c, PixelJsonWriter *writer, const char *text) {
    for (; *text; text++) pixel_json_char(c,writer,(unsigned char)*text);
}
static void pixel_json_quote(PixelContext *c, PixelJsonWriter *writer, PixelString *text) {
    pixel_json_char(c,writer,'"');
    for (size_t i=0; i<text->length; i++) {
        uint32_t ch=text->data[i];
        if (ch>0x10ffff || (ch>=0xd800 && ch<=0xdfff)) pixel_fail(c,"JSON strings require Unicode scalar values");
        if (ch=='"' || ch=='\\') { pixel_json_char(c,writer,'\\'); pixel_json_char(c,writer,ch); }
        else if (ch==8 || ch==9 || ch==10 || ch==12 || ch==13) {
            pixel_json_char(c,writer,'\\'); pixel_json_char(c,writer,ch==8 ? 'b' : ch==9 ? 't' : ch==10 ? 'n' : ch==12 ? 'f' : 'r');
        } else if (ch<32) {
            const char *hex="0123456789abcdef";
            pixel_json_ascii(c,writer,"\\u00"); pixel_json_char(c,writer,hex[ch>>4]); pixel_json_char(c,writer,hex[ch&15]);
        } else pixel_json_char(c,writer,ch);
    }
    pixel_json_char(c,writer,'"');
}
static void pixel_json_emit(PixelContext *c, PixelJsonWriter *writer, PixelValue value, unsigned depth) {
    if (pixel_cancelled) pixel_fail(c,"Execution cancelled");
    pixel_check_type(c,value,PIXEL_JSON);
    if (++writer->nodes>100000 || depth>128) pixel_fail(c,"JSON structure budget exceeded");
    PixelObject *node=value.array;
    switch (node->tag) {
        case PIXEL_JSON_NULL: pixel_json_ascii(c,writer,"null"); break;
        case PIXEL_JSON_BOOL: pixel_json_ascii(c,writer,node->data[0].integer ? "true" : "false"); break;
        case PIXEL_JSON_NUMBER:
            for (size_t i=0; i<node->data[0].string->length; i++) pixel_json_char(c,writer,node->data[0].string->data[i]);
            break;
        case PIXEL_JSON_STRING: pixel_json_quote(c,writer,node->data[0].string); break;
        case PIXEL_JSON_ARRAY: case PIXEL_JSON_OBJECT: {
            int object=node->tag==PIXEL_JSON_OBJECT;
            pixel_json_char(c,writer,object ? '{' : '[');
            for (size_t i=0; i<node->length; i++) {
                if (i) pixel_json_char(c,writer,',');
                if (object) { pixel_json_quote(c,writer,node->data[i++].string); pixel_json_char(c,writer,':'); }
                pixel_json_emit(c,writer,node->data[i],depth+1);
            }
            pixel_json_char(c,writer,object ? '}' : ']'); break;
        }
        default: pixel_fail(c,"Invalid JSON value tag");
    }
}
static PixelValue pixel_json_stringify(PixelContext *c, PixelValue value) {
    PixelJsonWriter writer={0}; pixel_json_emit(c,&writer,value,0);
    PixelValue result=pixel_string_new(c,writer.length);
    writer=(PixelJsonWriter){.output=(uint32_t *)result.string->data}; pixel_json_emit(c,&writer,value,0);
    return result;
}
static PixelValue pixel_json_convert(PixelContext *c, PixelValue value, unsigned depth, size_t *nodes) {
    if (pixel_cancelled) pixel_fail(c,"Execution cancelled");
    if (++*nodes>100000 || depth>128) pixel_fail(c,"JSON structure budget exceeded");
    if (value.kind==PIXEL_JSON) return pixel_retain(value);
    if (value.kind==PIXEL_INT || value.kind==PIXEL_FLOAT)
        return pixel_json_scalar(c,PIXEL_JSON_NUMBER,pixel_decimal_spelling(c,value));
    if (value.kind==PIXEL_STRING) return pixel_json_scalar(c,PIXEL_JSON_STRING,pixel_retain(value));
    if (value.kind==PIXEL_BOOL) return pixel_json_scalar(c,PIXEL_JSON_BOOL,value);
    if (value.kind==PIXEL_CALLABLE) pixel_fail(c,"Function values cannot be encoded as JSON");
    if (value.kind==PIXEL_ARRAY) {
        PixelValue result=pixel_json_node(c,PIXEL_JSON_ARRAY,value.array->length);
        for (size_t i=0; i<value.array->length; i++) result.array->data[i]=pixel_json_convert(c,pixel_item(value.array,i),depth+1,nodes);
        return result;
    }
    if (value.kind==PIXEL_RECORD || value.kind==PIXEL_ENUM) {
        int option=value.kind==PIXEL_ENUM ? pixel_json_option_type(value.array->type) : 0;
        if (option==2) pixel_fail(c,"Adjacent nested Option types have ambiguous JSON representation");
        if (option) return value.array->length ? pixel_json_convert(c,value.array->data[0],depth+1,nodes) : pixel_json_node(c,PIXEL_JSON_NULL,0);
        PixelValue fields=pixel_json_node(c,PIXEL_JSON_OBJECT,value.array->length*2);
        for (size_t i=0; i<value.array->length; i++) {
            fields.array->data[2*i]=pixel_retain((PixelValue){.kind=PIXEL_STRING,.string=value.array->names[i]});
            fields.array->data[2*i+1]=pixel_json_convert(c,value.array->data[i],depth+1,nodes);
        }
        if (value.kind==PIXEL_RECORD) return fields;
        PixelValue result=pixel_json_node(c,PIXEL_JSON_OBJECT,4);
        result.array->data[0]=pixel_ascii(c,"variant");
        result.array->data[1]=pixel_json_scalar(c,PIXEL_JSON_STRING,pixel_retain((PixelValue){.kind=PIXEL_STRING,.string=value.array->variant}));
        result.array->data[2]=pixel_ascii(c,"fields"); result.array->data[3]=fields; return result;
    }
    if (value.kind==PIXEL_MAP) {
        int object=value.array->tag;
        PixelValue result=pixel_json_node(c,object ? PIXEL_JSON_OBJECT : PIXEL_JSON_ARRAY,value.array->length*(object ? 2 : 1));
        size_t output=0;
        for (size_t i=pixel_map_next(value.array,0); i<value.array->data[1].array->length; i=pixel_map_next(value.array,i+1)) {
            PixelObject *pair=pixel_map_pair(value.array,i);
            if (object) {
                result.array->data[output++]=pixel_retain(pair->data[0]);
                result.array->data[output++]=pixel_json_convert(c,pair->data[1],depth+1,nodes);
            } else {
                if (++*nodes>100000) pixel_fail(c,"JSON structure budget exceeded");
                PixelValue entry=pixel_json_node(c,PIXEL_JSON_OBJECT,4);
                entry.array->data[0]=pixel_ascii(c,"key"); entry.array->data[1]=pixel_json_convert(c,pair->data[0],depth+2,nodes);
                entry.array->data[2]=pixel_ascii(c,"value"); entry.array->data[3]=pixel_json_convert(c,pair->data[1],depth+2,nodes);
                result.array->data[output++]=entry;
            }
        }
        return result;
    }
    pixel_fail(c,"Value cannot be represented as JSON"); return (PixelValue){0};
}
static PixelValue pixel_json_from(PixelContext *c, PixelValue value) {
    size_t nodes=0; PixelValue result=pixel_json_convert(c,value,0,&nodes);
    PixelJsonWriter writer={0}; pixel_json_emit(c,&writer,result,0); return result;
}
static size_t pixel_json_length(PixelContext *c, PixelValue value) {
    switch (value.array->tag) {
        case PIXEL_JSON_STRING: return value.array->data[0].string->length;
        case PIXEL_JSON_ARRAY: return value.array->length;
        case PIXEL_JSON_OBJECT: return value.array->length/2;
        default: pixel_fail(c,"JSON length requires array, object or string"); return 0;
    }
}

typedef struct {
    PixelContext *context; PixelValue text; size_t offset,nodes;
    PixelDecoded error;
} PixelJsonParser;
static void pixel_json_parse_error(PixelJsonParser *p, const char *code, const char *message) {
    if (!p->error.code) p->error=(PixelDecoded){.kind="json",.code=code,.message=message};
}
static void pixel_json_space(PixelJsonParser *p) {
    while (p->offset<p->text.string->length) {
        uint32_t ch=p->text.string->data[p->offset];
        if (ch!=' ' && ch!='\t' && ch!='\n' && ch!='\r') break;
        p->offset++;
    }
}
static int pixel_json_hex(PixelJsonParser *p, size_t *offset) {
    int value=0;
    if (p->text.string->length-*offset<4) return -1;
    for (unsigned i=0; i<4; i++) {
        uint32_t ch=p->text.string->data[(*offset)++];
        int digit=ch>='0' && ch<='9' ? (int)(ch-'0') : ch>='a' && ch<='f' ? (int)(ch-'a')+10 : ch>='A' && ch<='F' ? (int)(ch-'A')+10 : -1;
        if (digit<0) return -1;
        value=value*16+digit;
    }
    return value;
}
static size_t pixel_json_string_scan(PixelJsonParser *p, size_t start, uint32_t *out, size_t *end) {
    size_t i=start,count=0,length=p->text.string->length; const uint32_t *data=p->text.string->data;
    while (i<length) {
        if (pixel_cancelled) pixel_fail(p->context,"Execution cancelled");
        uint32_t ch=data[i++];
        if (ch=='"') { *end=i; return count; }
        if (ch<32) break;
        if (ch=='\\') {
            if (i==length) break;
            ch=data[i++];
            if (ch=='b') ch=8;
            else if (ch=='f') ch=12;
            else if (ch=='n') ch=10;
            else if (ch=='r') ch=13;
            else if (ch=='t') ch=9;
            else if (ch=='u') {
                int decoded=pixel_json_hex(p,&i); if (decoded<0) break;
                ch=(uint32_t)decoded;
                if (ch>=0xd800 && ch<=0xdbff) {
                    if (length-i<2 || data[i]!='\\' || data[i+1]!='u') goto invalid_scalar;
                    i+=2; int low=pixel_json_hex(p,&i);
                    if (low<0) break;
                    if (low<0xdc00 || low>0xdfff) goto invalid_scalar;
                    ch=0x10000+((ch-0xd800)<<10)+(uint32_t)low-0xdc00;
                }
            } else if (ch!='"' && ch!='\\' && ch!='/') break;
        }
        if (ch>0x10ffff || (ch>=0xd800 && ch<=0xdfff)) goto invalid_scalar;
        if (out) out[count]=ch;
        count++;
    }
    pixel_json_parse_error(p,"json.invalid_syntax","Invalid JSON string"); return SIZE_MAX;
invalid_scalar:
    pixel_json_parse_error(p,"json.invalid_value","JSON strings require Unicode scalar values"); return SIZE_MAX;
}
static PixelValue pixel_json_parse_string(PixelJsonParser *p) {
    if (p->offset==p->text.string->length || p->text.string->data[p->offset]!='"') {
        pixel_json_parse_error(p,"json.invalid_syntax","Expected JSON string"); return (PixelValue){0};
    }
    size_t end=0,start=++p->offset;
    size_t count=pixel_json_string_scan(p,start,NULL,&end);
    if (count==SIZE_MAX) return (PixelValue){0};
    PixelValue value=pixel_string_new(p->context,count);
    pixel_json_string_scan(p,start,(uint32_t *)value.string->data,&end); p->offset=end; return value;
}
static int pixel_json_digit(uint32_t ch) { return ch>='0' && ch<='9'; }
static uint64_t pixel_json_key_hash(PixelContext *c, PixelString *key) {
    /* JSON limits count Unicode scalars, independently of typed-map key bytes. */
    uint64_t hash=UINT64_C(14695981039346656037);
    for (size_t i=0; i<key->length; i++) {
        if (pixel_cancelled) pixel_fail(c,"Execution cancelled");
        hash=pixel_hash_word(hash,key->data[i]);
    }
    return hash;
}
static PixelValue pixel_json_parse_value(PixelJsonParser *p, unsigned depth) {
    PixelContext *c=p->context; const uint32_t *data=p->text.string->data; size_t length=p->text.string->length;
    if (pixel_cancelled) pixel_fail(c,"Execution cancelled");
    if (++p->nodes>100000 || depth>128) {
        pixel_json_parse_error(p,"json.invalid_value","JSON structure budget exceeded"); return (PixelValue){0};
    }
    pixel_json_space(p);
    if (p->offset==length) goto invalid;
    uint32_t ch=data[p->offset];
    if (ch=='"') {
        PixelValue text=pixel_json_parse_string(p);
        return p->error.code ? (PixelValue){0} : pixel_json_scalar(c,PIXEL_JSON_STRING,text);
    }
    if (ch=='[' || ch=='{') {
        int object=ch=='{'; uint32_t close=object ? '}' : ']';
        PixelValue *items=NULL,index={0}; size_t count=0,capacity=0;
        if (object) index=pixel_map_new(c,0,1);
        p->offset++; pixel_json_space(p);
        if (p->offset<length && data[p->offset]==close) p->offset++;
        else for (;;) {
            PixelValue key={0};
            if (object) {
                key=pixel_json_parse_string(p); if (p->error.code) break;
                uint64_t hash=pixel_json_key_hash(c,key.string);
                if (pixel_map_find_hash(c,index.array,key,hash)!=SIZE_MAX) {
                    pixel_release(c,key); pixel_json_parse_error(p,"json.duplicate_key","Duplicate JSON object key"); break;
                }
                PixelValue updated=pixel_map_insert(c,index.array->data[0],key,hash,0);
                pixel_release(c,index.array->data[0]); index.array->data[0]=updated;
                pixel_json_space(p);
                if (p->offset==length || data[p->offset++]!=':') {
                    pixel_release(c,key); pixel_json_parse_error(p,"json.invalid_syntax","Expected JSON member separator"); break;
                }
            }
            PixelValue child=pixel_json_parse_value(p,depth+1);
            if (p->error.code) { pixel_release(c,key); break; }
            size_t needed=count+(object ? 2 : 1);
            if (needed>capacity) {
                capacity=capacity ? capacity*2 : 8;
                PixelValue *grown=realloc(items,capacity*sizeof(*items));
                if (!grown) pixel_fail(c,"Allocation failed");
                items=grown;
            }
            if (object) items[count++]=key;
            items[count++]=child;
            pixel_json_space(p);
            if (p->offset==length) { pixel_json_parse_error(p,"json.invalid_syntax","Unterminated JSON container"); break; }
            ch=data[p->offset++]; if (ch==close) break;
            if (ch!=',') { pixel_json_parse_error(p,"json.invalid_syntax","Expected JSON item separator"); break; }
            pixel_json_space(p);
        }
        pixel_release(c,index);
        if (p->error.code) {
            for (size_t i=0; i<count; i++) pixel_release(c,items[i]);
            free(items); return (PixelValue){0};
        }
        PixelValue result=pixel_json_node(c,object ? PIXEL_JSON_OBJECT : PIXEL_JSON_ARRAY,count);
        for (size_t i=0; i<count; i++) result.array->data[i]=items[i];
        free(items); return result;
    }
    if (ch=='n' || ch=='t' || ch=='f') {
        const char *word=ch=='n' ? "null" : ch=='t' ? "true" : "false";
        for (size_t i=0; word[i]; i++) if (p->offset==length || data[p->offset++]!=(unsigned char)word[i]) goto invalid;
        return ch=='n' ? pixel_json_node(c,PIXEL_JSON_NULL,0) : pixel_json_scalar(c,PIXEL_JSON_BOOL,pixel_value(PIXEL_BOOL,ch=='t'));
    }
    if (ch=='-' || pixel_json_digit(ch)) {
        size_t start=p->offset;
        if (ch=='-') p->offset++;
        if (p->offset==length || !pixel_json_digit(data[p->offset])) goto invalid;
        if (data[p->offset]=='0') p->offset++;
        else while (p->offset<length && pixel_json_digit(data[p->offset])) p->offset++;
        if (p->offset<length && data[p->offset]=='.') {
            size_t first=++p->offset;
            while (p->offset<length && pixel_json_digit(data[p->offset])) p->offset++;
            if (p->offset==first) goto invalid;
        }
        if (p->offset<length && (data[p->offset]=='e' || data[p->offset]=='E')) {
            p->offset++;
            if (p->offset<length && (data[p->offset]=='+' || data[p->offset]=='-')) p->offset++;
            size_t first=p->offset;
            while (p->offset<length && pixel_json_digit(data[p->offset])) p->offset++;
            if (p->offset==first) goto invalid;
        }
        return pixel_json_scalar(c,PIXEL_JSON_NUMBER,pixel_string_slice(c,p->text,(int64_t)start,(int64_t)p->offset));
    }
invalid:
    pixel_json_parse_error(p,"json.invalid_syntax","Invalid JSON syntax"); return (PixelValue){0};
}
static PixelDecoded pixel_json_parse(PixelContext *c, PixelValue text) {
    PixelJsonParser parser={.context=c,.text=text};
    PixelValue value=pixel_json_parse_value(&parser,0); pixel_json_space(&parser);
    if (!parser.error.code && parser.offset!=text.string->length)
        pixel_json_parse_error(&parser,"json.invalid_syntax","Trailing JSON input");
    if (parser.error.code) { pixel_release(c,value); return parser.error; }
    return (PixelDecoded){.value=value};
}
static PixelDecoded pixel_json_require(PixelValue value, int tag) {
    if (value.array->tag==tag) return (PixelDecoded){0};
    return (PixelDecoded){.kind="json",.code="json.type_mismatch",.message="JSON value has the wrong kind",
        .expected=pixel_json_kind_name(tag),.actual=pixel_json_kind_name(value.array->tag)};
}
static PixelDecoded pixel_json_numeric_error(PixelValue spelling, int real) {
    return (PixelDecoded){.kind="json",.code="json.numeric_range",
        .message=real ? "JSON number exceeds finite float64 range" : "JSON number is not an exact signed 64-bit integer",
        .expected=real ? "float64" : "int",.actual_value=spelling};
}
/* Decimal-to-int conversion never passes through a floating point value. */
static PixelDecoded pixel_json_integer(PixelValue spelling) {
    const uint32_t *s=spelling.string->data; size_t n=spelling.string->length,i=0,digits=0,first=SIZE_MAX,last=0,fraction=0;
    int negative=s[0]=='-',dot=0; if (negative) i++;
    for (; i<n && s[i]!='e' && s[i]!='E'; i++) {
        if (s[i]=='.') { dot=1; continue; }
        if (s[i]!='0') { if (first==SIZE_MAX) first=digits; last=digits; }
        digits++; if (dot) fraction++;
    }
    if (first==SIZE_MAX) return (PixelDecoded){.value=pixel_value(PIXEL_INT,0)};
    int64_t exponent=0; int exponent_negative=0;
    if (i<n) {
        i++; if (i<n && (s[i]=='+' || s[i]=='-')) { exponent_negative=s[i]=='-'; i++; }
        for (; i<n; i++) if (exponent<2000000) exponent=exponent*10+(s[i]-'0');
    }
    if (exponent_negative) exponent=-exponent;
    int64_t scale=exponent-(int64_t)fraction,integer_digits=(int64_t)(digits-first)+scale;
    if (integer_digits<=0 || integer_digits>19 || (scale<0 && (int64_t)last>=(int64_t)digits+scale))
        return pixel_json_numeric_error(spelling,0);
    size_t keep=scale<0 ? (size_t)((int64_t)digits+scale) : digits,position=0;
    uint64_t number=0,limit=negative ? UINT64_C(9223372036854775808) : INT64_MAX;
    for (i=(size_t)negative; i<n && s[i]!='e' && s[i]!='E'; i++) {
        if (s[i]=='.') continue;
        if (position>=first && position<keep) {
            unsigned digit=s[i]-'0';
            if (number>(limit-digit)/10) return pixel_json_numeric_error(spelling,0);
            number=number*10+digit;
        }
        position++;
    }
    for (int64_t zero=0; zero<scale; zero++) {
        if (number>limit/10) return pixel_json_numeric_error(spelling,0);
        number*=10;
    }
    int64_t result=negative ? (number==UINT64_C(9223372036854775808) ? INT64_MIN : -(int64_t)number) : (int64_t)number;
    return (PixelDecoded){.value=pixel_value(PIXEL_INT,result)};
}
/* get, at, keys, asString, asBool, asInt, asFloat64. */
static PixelDecoded pixel_json_access(PixelContext *c, PixelValue value, PixelValue argument, int operation, int strings_type) {
    int tag=operation==0 || operation==2 ? PIXEL_JSON_OBJECT : operation==1 ? PIXEL_JSON_ARRAY :
            operation==3 ? PIXEL_JSON_STRING : operation==4 ? PIXEL_JSON_BOOL : PIXEL_JSON_NUMBER;
    PixelDecoded error=pixel_json_require(value,tag); if (error.code) return error;
    PixelObject *node=value.array;
    if (operation==0) {
        for (size_t i=0; i<node->length; i+=2) {
            if (pixel_cancelled) pixel_fail(c,"Execution cancelled");
            if (!pixel_string_compare(node->data[i].string,argument.string)) return (PixelDecoded){.value=pixel_retain(node->data[i+1])};
        }
        return (PixelDecoded){.kind="json",.code="json.missing_key",.message="Missing JSON key",.path=argument};
    }
    if (operation==1) {
        if (argument.integer<0 || (uint64_t)argument.integer>=node->length)
            return (PixelDecoded){.kind="json",.code="json.index_bounds",.message="JSON array index out of bounds"};
        return (PixelDecoded){.value=pixel_retain(node->data[(size_t)argument.integer])};
    }
    if (operation==2) {
        PixelValue result=pixel_array_new(c,node->length/2,strings_type);
        for (size_t i=0; i<node->length; i+=2) pixel_array_init(result.array,i/2,pixel_retain(node->data[i]));
        return (PixelDecoded){.value=result};
    }
    if (operation==3 || operation==4) return (PixelDecoded){.value=pixel_retain(node->data[0])};
    if (operation==5) return pixel_json_integer(node->data[0]);
    PixelDecoded result=pixel_parse_number(c,node->data[0],1);
    return result.code ? pixel_json_numeric_error(node->data[0],1) : result;
}
