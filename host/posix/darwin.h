/* (macos) What host/posix's Linux code asks of the kernel and glibc, on macOS: included by win32_posix.c, prof.c and
   the host files that take the Linux route. Linux builds never see this file.

   - futexes: os_sync_wait_on_address (macOS 14.4 and later), the same compare-and-wait on a 32-bit word;
   - thread ids: pthread_threadid_np's, cut to 32 bits (they count up from the boot, so a process's are distinct);
   - no CPU affinity (the kernel places threads; Apple silicon has no call to pin one), so the masks say "every core"
     and setting one succeeds without effect;
   - condition variables wait on the monotonic clock through pthread_cond_timedwait_relative_np
     (there is no pthread_condattr_setclock);
   - mmap has no MAP_FIXED_NOREPLACE: the address is a hint, and every caller checks what it got. */
#ifndef HOOKSHOT_POSIX_DARWIN_H
#define HOOKSHOT_POSIX_DARWIN_H
#ifdef __APPLE__
#include <errno.h>
#include <mach/mach.h>
#include <mach/mach_time.h>
#include <mach/mach_vm.h>
#include <mach-o/dyld.h>
#include <os/os_sync_wait_on_address.h>
#include <pthread.h>
#include <stdint.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/sysctl.h>
#include <time.h>
#include <unistd.h>

#ifndef MAP_FIXED_NOREPLACE
#define MAP_FIXED_NOREPLACE 0
#endif
#ifndef MAP_STACK
#define MAP_STACK 0
#endif
#ifndef MAP_NORESERVE
#define MAP_NORESERVE 0
#endif

#define FUTEX_WAIT_PRIVATE 0
#define FUTEX_WAKE_PRIVATE 1
static inline long hos_darwin_futex(volatile uint32_t *a, int op, uint32_t v, const struct timespec *ts) {
    if (op == FUTEX_WAKE_PRIVATE) {
        if (v == 1) os_sync_wake_by_address_any((void *)a, 4, OS_SYNC_WAKE_BY_ADDRESS_NONE);
        else os_sync_wake_by_address_all((void *)a, 4, OS_SYNC_WAKE_BY_ADDRESS_NONE);
        return 0;   /* (ENOENT, nobody waiting, is no error to a futex wake) */
    }
    if (!ts) return os_sync_wait_on_address((void *)a, v, 4, OS_SYNC_WAIT_ON_ADDRESS_NONE) < 0 ? -1 : 0;
    const uint64_t ns = (uint64_t)ts->tv_sec * 1000000000ull + (uint64_t)ts->tv_nsec;
    if (!ns) {   /* (a zero timeout is EINVAL there: a futex says the time is up when the word still holds v) */
        errno = __atomic_load_n(a, __ATOMIC_SEQ_CST) == v ? ETIMEDOUT : EAGAIN;
        return -1;
    }
    return os_sync_wait_on_address_with_timeout((void *)a, v, 4, OS_SYNC_WAIT_ON_ADDRESS_NONE, OS_CLOCK_MACH_ABSOLUTE_TIME, ns) < 0 ? -1 : 0;
}

static inline uint32_t hos_darwin_gettid(void) {
    uint64_t id = 0;
    pthread_threadid_np(NULL, &id);
    return (uint32_t)id ? (uint32_t)id : 1;
}

/* (an absolute CLOCK_MONOTONIC time, as Linux's clock_nanosleep(TIMER_ABSTIME) takes) */
static inline int hos_darwin_sleep_until(const struct timespec *until) {
    for (;;) {
        struct timespec now;
        clock_gettime(CLOCK_MONOTONIC, &now);
        int64_t ns = (int64_t)(until->tv_sec - now.tv_sec) * 1000000000ll + (until->tv_nsec - now.tv_nsec);
        if (ns <= 0) return 0;
        struct timespec rel = {(time_t)(ns / 1000000000ll), (long)(ns % 1000000000ll)};
        if (nanosleep(&rel, NULL) == 0) return 0;
        if (errno != EINTR) return errno;
    }
}

/* (pthread_cond_timedwait to an absolute CLOCK_MONOTONIC time) */
static inline int hos_darwin_cond_wait_until(pthread_cond_t *cv, pthread_mutex_t *mu, const struct timespec *until) {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    int64_t ns = (int64_t)(until->tv_sec - now.tv_sec) * 1000000000ll + (until->tv_nsec - now.tv_nsec);
    if (ns <= 0) return ETIMEDOUT;
    struct timespec rel = {(time_t)(ns / 1000000000ll), (long)(ns % 1000000000ll)};
    return pthread_cond_timedwait_relative_np(cv, mu, &rel);
}

static inline int hos_darwin_sysctl_int(const char *name, int fallback) {
    int v = 0;
    size_t n = sizeof v;
    return sysctlbyname(name, &v, &n, NULL, 0) == 0 && n == sizeof v ? v : fallback;
}

/* (the host's page: 16 KB on Apple silicon, where the guest's and Windows' is 4 KB. What is protected or given back
   is rounded to it) */
static inline uintptr_t hos_darwin_page(void) {
    static uintptr_t page;
    if (!page) page = (uintptr_t)getpagesize();
    return page;
}

/* Pages given back so that they read as zero. madvise(MADV_DONTNEED) is only advice on macOS and the pages keep what
   they hold, and the host's page is 16 KB where the range is made of the guest's 4 KB pages. So: the whole host pages
   inside the range are mapped anew (fresh zero pages; the old ones go back to the system), and what is left at either
   end is cleared by hand. Private anonymous memory only, which is what the guest window and VirtualAlloc's are. */
static inline int hos_darwin_discard(void *p, size_t n) {
    const uintptr_t hp = hos_darwin_page(), a = (uintptr_t)p, b = a + n;
    const uintptr_t lo = (a + hp - 1) & ~(hp - 1), hi = b & ~(hp - 1);
    if (hi <= lo) {
        memset(p, 0, n);
        return 0;
    }
    if (lo > a) memset((void *)a, 0, lo - a);
    if (b > hi) memset((void *)hi, 0, b - hi);
    if (mmap((void *)lo, hi - lo, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON | MAP_FIXED, -1, 0) == MAP_FAILED)
        memset((void *)lo, 0, hi - lo);
    return 0;
}
#endif
#endif
