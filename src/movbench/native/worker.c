#define _GNU_SOURCE
#include <errno.h>
#include <inttypes.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <time.h>
#include <unistd.h>

typedef void (*kernel_fn)(const uint8_t *, uint8_t *, const uint8_t *, uint8_t *);
typedef struct { uint8_t *mapping, *data; size_t total, capacity, size; } buffer;
typedef struct { kernel_fn fn; buffer code, tables, scratch, input, output; } kernel;
static size_t page;
static volatile uint64_t sink;

static void die(const char *message) { fprintf(stderr, "%s: %s\n", message, strerror(errno)); exit(2); }
static uint64_t now_ns(void) {
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC_RAW, &t)) die("clock_gettime");
    return (uint64_t)t.tv_sec * 1000000000ULL + t.tv_nsec;
}
static buffer allocate(size_t size) {
    size_t capacity = ((size ? size : 1) + page - 1) / page * page;
    buffer b = {.size=size, .capacity=capacity, .total=capacity+2*page};
    b.mapping = mmap(NULL, b.total, PROT_NONE, MAP_PRIVATE|MAP_ANONYMOUS, -1, 0);
    if (b.mapping == MAP_FAILED) die("mmap");
    if (mprotect(b.mapping+page, capacity, PROT_READ|PROT_WRITE)) die("mprotect");
    // Every allocation ends exactly at a guard page. Undersized tables have no
    // accessible tail; the left padding is a canary, not advertised storage.
    b.data = b.mapping + page + capacity - size;
    memset(b.mapping+page, 0xa5, capacity);
    return b;
}
static buffer read_file(const char *path) {
    FILE *f = fopen(path, "rb");
    if (!f) die("open file");
    if (fseek(f, 0, SEEK_END)) die("seek");
    long length = ftell(f);
    if (length < 0) die("file length");
    rewind(f);
    buffer b = allocate((size_t)length);
    if (fread(b.data, 1, b.size, f) != b.size) die("read file");
    fclose(f);
    return b;
}
static void protect(buffer b, int flags) {
    if (mprotect(b.mapping+page, b.capacity, flags)) die("mprotect");
}
static int intact(buffer b) {
    for (uint8_t *p=b.mapping+page; p<b.data; ++p) if (*p != 0xa5) return 0;
    return 1;
}
static kernel load_kernel(char **args, size_t input_bytes, size_t output_bytes) {
    kernel k = {0};
    k.code = read_file(args[0]);
    protect(k.code, PROT_READ|PROT_EXEC);
    k.fn = (kernel_fn)(void *)k.code.data;
    k.tables = read_file(args[1]);
    protect(k.tables, PROT_READ);
    k.scratch = allocate(strtoull(args[2], NULL, 10));
    k.input = allocate(input_bytes);
    k.output = allocate(output_bytes);
    return k;
}
static void invoke(kernel *k) { k->fn(k->input.data, k->output.data, k->tables.data, k->scratch.data); }
static int verify(kernel *k, const uint8_t *cases, size_t count, size_t in, size_t out) {
    size_t stride = in+out;
    for (size_t pass=0; pass<2; ++pass) {
        for (size_t i=0; i<count; ++i) {
            const uint8_t *item = cases + (pass ? count-1-i : i)*stride;
            protect(k->input, PROT_READ|PROT_WRITE);
            memcpy(k->input.data, item, in);
            protect(k->input, PROT_READ);
            // Different scratch histories expose reliance on initial zeros.
            if (!pass) memset(k->scratch.data, (int)(i*29+17)&255, k->scratch.size);
            for (size_t j=0; j<out; ++j) k->output.data[j] = item[in+j] ^ 255;
            invoke(k);
            if (memcmp(k->output.data, item+in, out) || !intact(k->output) || !intact(k->scratch)) {
                fprintf(stderr, "Wrong output or memory overwrite at case %zu, pass %zu\n", i, pass);
                return 0;
            }
        }
    }
    return 1;
}
// The timed workload cycles through 64 representative seeded inputs. Input
// copies, dispatch and loop overhead are included identically for both kernels.
static uint64_t batch(kernel *k, const uint8_t *cases, size_t count, size_t in, size_t out,
                      uint64_t iterations) {
    size_t pool = count < 64 ? count : 64;
    protect(k->input, PROT_READ|PROT_WRITE);
    uint64_t start = now_ns();
    for (uint64_t i=0; i<iterations; ++i) {
        memcpy(k->input.data, cases+(i%pool)*(in+out), in);
        invoke(k);
    }
    uint64_t elapsed = now_ns()-start;
    sink += k->output.data[0];
    return elapsed;
}
static uint64_t calibrate(kernel *k, const uint8_t *cases, size_t count, size_t in,
                          size_t out, uint64_t target) {
    uint64_t iterations=64;
    while (iterations < (1ULL<<30)) {
        if (batch(k,cases,count,in,out,iterations)>=target) break;
        iterations*=2;
    }
    return iterations;
}
int main(int argc, char **argv) {
    if (argc != 12) { fprintf(stderr, "Expected candidate/reference paths, fixtures, repeats, ms, cpu, seed\n"); return 2; }
    struct rlimit core = {0,0}; setrlimit(RLIMIT_CORE, &core);
    struct rlimit address = {512ULL*1024*1024,512ULL*1024*1024}; setrlimit(RLIMIT_AS, &address);
    struct rlimit cpu_limit = {30,30}; setrlimit(RLIMIT_CPU, &cpu_limit);
    page = (size_t)sysconf(_SC_PAGESIZE);
    int cpu = atoi(argv[10]);
    cpu_set_t set; CPU_ZERO(&set); CPU_SET(cpu, &set);
    if (sched_setaffinity(0, sizeof(set), &set)) die("CPU affinity");
    buffer fixtures = read_file(argv[7]);
    if (fixtures.size < 12) { fprintf(stderr, "Invalid fixture header\n"); return 2; }
    uint32_t header[3]; memcpy(header, fixtures.data, sizeof(header));
    size_t count=header[0], in=header[1], out=header[2];
    if (!count || !in || !out || count > 100000 || in > 65536 || out > 65536 ||
        fixtures.size != 12+count*(in+out)) { fprintf(stderr, "Invalid fixtures\n"); return 2; }
    const uint8_t *cases = fixtures.data+12;
    kernel candidate = load_kernel(argv+1, in, out), reference = load_kernel(argv+4, in, out);
    if (!verify(&reference, cases, count, in, out)) { fprintf(stderr, "Reference failed\n"); return 3; }
    if (!verify(&candidate, cases, count, in, out)) return 4;
    int repeats = atoi(argv[8]);
    uint64_t target = strtoull(argv[9],NULL,10)*1000000ULL;
    unsigned long seed = strtoul(argv[11],NULL,10);
    if (repeats < 3 || repeats > 99 || target < 1000000 || target > 1000000000) return 2;
    // Each implementation receives a full-duration batch, even for enormous
    // speedups over the Boolean-circuit reference. Both counts are multiples
    // of 64, so the rotating input distributions are identical.
    uint64_t ai=calibrate(&candidate,cases,count,in,out,target);
    uint64_t bi=calibrate(&reference,cases,count,in,out,target);
    double *a=calloc((size_t)repeats,sizeof(double)), *b=calloc((size_t)repeats,sizeof(double));
    if (!a || !b) die("calloc");
    for (int r=0; r<repeats; ++r) {
        if ((r+(int)(seed&1))&1) {
            b[r]=(double)batch(&reference,cases,count,in,out,bi)/bi;
            a[r]=(double)batch(&candidate,cases,count,in,out,ai)/ai;
        } else {
            a[r]=(double)batch(&candidate,cases,count,in,out,ai)/ai;
            b[r]=(double)batch(&reference,cases,count,in,out,bi)/bi;
        }
    }
    // Check after timing as well, including the exact stateful workload used
    // during calibration and measurement.
    if (!verify(&candidate,cases,count,in,out)) return 4;
    printf("{\"valid\":true,\"cases\":%zu,\"iterations_per_trial\":{\"candidate\":%" PRIu64 ",\"reference\":%" PRIu64 "},\"candidate_ns\":[",count,ai,bi);
    for(int r=0;r<repeats;++r) printf("%s%.6f",r?",":"",a[r]);
    printf("],\"reference_ns\":[");
    for(int r=0;r<repeats;++r) printf("%s%.6f",r?",":"",b[r]);
    printf("]}\n");
    return 0;
}
