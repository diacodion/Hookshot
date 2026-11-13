/* (macos) host/posix/prof.c's thread profilers on macOS, with the same variables and the same lines in the log:
   HOS_PROFILE=<seconds> (the busiest threads) and HOS_THREAD_TIMES=<seconds> (threads over 5% of a core). The threads
   and their CPU times come from Mach (task_threads, thread_info), the names from the guest's thread list, else the
   name the thread gave itself (SetThreadDescription -> pthread_setname_np).

   Not here: HOS_SAMPLE / HOS_SAMPLE_ALL (prof.c sends each thread a real-time signal and unwinds in its handler;
   macOS's own `sample <pid>` and Instruments do that from outside, with symbols) and HOS_WATCH (perf_event_open's
   data breakpoints, which macOS has no call for). They are said once in the log when asked for. */
#include "../hos.h"
#include "darwin.h"
#include <mach/thread_info.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>

int hos_thread_handles(HANDLE *out, const char **names, int max);   /* hos_thread.c */
void hos_prof_start(void);

typedef struct { uint64_t tid, cpu_ns; char name[64]; } Task;

static int tasks(Task *out, int max) {
    static HANDLE gh[1024];
    static const char *gn[1024];
    const int ng = hos_thread_handles(gh, gn, 1024);
    static uint32_t gtid[1024];
    for (int i = 0; i < ng; i++) gtid[i] = (uint32_t)GetThreadId(gh[i]);
    thread_act_array_t list = NULL;
    mach_msg_type_number_t count = 0;
    if (task_threads(mach_task_self(), &list, &count) != KERN_SUCCESS) return 0;
    int n = 0;
    for (mach_msg_type_number_t i = 0; i < count; i++) {
        thread_basic_info_data_t bi;
        mach_msg_type_number_t bn = THREAD_BASIC_INFO_COUNT;
        thread_identifier_info_data_t ii;
        mach_msg_type_number_t in = THREAD_IDENTIFIER_INFO_COUNT;
        thread_extended_info_data_t xi;
        mach_msg_type_number_t xn = THREAD_EXTENDED_INFO_COUNT;
        if (n < max && thread_info(list[i], THREAD_BASIC_INFO, (thread_info_t)&bi, &bn) == KERN_SUCCESS &&
            thread_info(list[i], THREAD_IDENTIFIER_INFO, (thread_info_t)&ii, &in) == KERN_SUCCESS) {
            Task *t = &out[n++];
            t->tid = ii.thread_id;
            t->cpu_ns = ((uint64_t)bi.user_time.seconds + (uint64_t)bi.system_time.seconds) * 1000000000ull +
                        ((uint64_t)bi.user_time.microseconds + (uint64_t)bi.system_time.microseconds) * 1000ull;
            t->name[0] = 0;
            for (int k = 0; k < ng; k++)   /* (GetThreadId is the thread id cut to 32 bits: darwin.h) */
                if (gtid[k] == (uint32_t)ii.thread_id) { snprintf(t->name, sizeof t->name, "%s", gn[k]); break; }
            if (!t->name[0] && thread_info(list[i], THREAD_EXTENDED_INFO, (thread_info_t)&xi, &xn) == KERN_SUCCESS)
                snprintf(t->name, sizeof t->name, "%s", xi.pth_name);
            if (!t->name[0]) snprintf(t->name, sizeof t->name, "tid %llu", (unsigned long long)t->tid);
        }
        mach_port_deallocate(mach_task_self(), list[i]);
    }
    vm_deallocate(mach_task_self(), (vm_address_t)list, count * sizeof *list);
    return n;
}

/* HOS_PROFILE=<seconds>: every interval, the busiest threads and their share of a core */
static DWORD WINAPI prof_thread(void *p) {
    const DWORD ms = (DWORD)(uintptr_t)p * 1000;
    enum { MAXT = 512 };
    static uint64_t ids[MAXT], was[MAXT];
    int nwas = 0;
    for (;;) {
        Sleep(ms);
        static Task cur[MAXT];
        static uint64_t delta[MAXT];
        const int n = tasks(cur, MAXT);
        for (int i = 0; i < n; i++) {
            delta[i] = cur[i].cpu_ns;
            for (int k = 0; k < nwas; k++)
                if (ids[k] == cur[i].tid) { delta[i] = cur[i].cpu_ns - was[k]; break; }
        }
        for (int i = 0; i < n; i++) ids[i] = cur[i].tid, was[i] = cur[i].cpu_ns;
        nwas = n;
        for (int i = 0; i < n; i++)   /* (busiest first) */
            for (int j = i + 1; j < n; j++)
                if (delta[j] > delta[i]) {
                    Task t = cur[i]; cur[i] = cur[j]; cur[j] = t;
                    const uint64_t d = delta[i]; delta[i] = delta[j]; delta[j] = d;
                }
        char line[1024];
        int len = snprintf(line, sizeof line, "prof: busiest threads (%% of a core):");
        uint64_t total = 0;
        for (int i = 0; i < n; i++) total += delta[i];
        for (int i = 0; i < n && i < 10 && len < (int)sizeof line - 80; i++)
            len += snprintf(line + len, sizeof line - len, " %s %.0f,", cur[i].name, delta[i] / (ms * 10000.0));
        snprintf(line + len, sizeof line - len, " all %.0f", total / (ms * 10000.0));
        hos_log("%s", line);
    }
    return 0;
}

/* HOS_THREAD_TIMES=<seconds>: every interval, the threads that used over 5% of a core */
static DWORD WINAPI thread_times_thread(void *p) {
    const DWORD ms = (DWORD)(uintptr_t)p * 1000;
    enum { MAXT = 512 };
    static struct { uint64_t tid, cpu; } last[MAXT];
    int nlast = 0;
    for (;;) {
        Sleep(ms);
        static Task now[MAXT];
        const int n = tasks(now, MAXT);
        char line[4096];
        int len = snprintf(line, sizeof line, "threads in %u s:", (unsigned)(ms / 1000));
        for (int i = 0; i < n; i++) {
            uint64_t before = 0;
            int seen = 0;
            for (int k = 0; k < nlast; k++)
                if (last[k].tid == now[i].tid) before = last[k].cpu, seen = 1;
            const double pct = seen ? (double)(now[i].cpu_ns - before) / (ms * 1e6) * 100.0 : 0;
            if (pct >= 5.0 && len < (int)sizeof line - 96) len += snprintf(line + len, sizeof line - len, " %s %.0f%%;", now[i].name, pct);
        }
        for (int i = 0; i < n; i++) last[i].tid = now[i].tid, last[i].cpu = now[i].cpu_ns;
        nlast = n;
        hos_log("prof: %s", line);
    }
    return 0;
}

void hos_prof_start(void) {
    const char *tt = getenv("HOS_THREAD_TIMES");
    if (tt && atoi(tt) > 0) CreateThread(NULL, 0, thread_times_thread, (void *)(uintptr_t)atoi(tt), 0, NULL);
    const char *e = getenv("HOS_PROFILE");
    if (e && atoi(e) > 0) CreateThread(NULL, 0, prof_thread, (void *)(uintptr_t)atoi(e), 0, NULL);
    const char *s = getenv("HOS_SAMPLE"), *all = getenv("HOS_SAMPLE_ALL"), *watch = getenv("HOS_WATCH");
    if ((s && *s) || (all && *all))
        hos_log("prof: HOS_SAMPLE and HOS_SAMPLE_ALL are not in the macOS build: `sample %d` or Instruments instead", (int)getpid());
    if (watch && *watch) hos_log("prof: HOS_WATCH is not in the macOS build (no data breakpoints from inside a process)");
}
