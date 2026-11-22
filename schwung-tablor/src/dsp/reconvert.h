/* Convert on load: presets the module's converter made are converted again from their originals when a newer converter arrives (converter/reconvert.py).
 *
 * The page's import stamps each preset file with the converter that made it ("conv", a hash of the converter bundle) and where its original is kept ("src").
 * An update brings a new converter (the module's converter/ folder, conv_version.json beside it). A preset whose stamp differs is converted again: at once
 * when it is chosen (urgent), and the rest in the background after the store is scanned. The work is done by one Python process per Move, shared by every
 * slot: reconvert.py --serve reads a preset path per line and answers a line of JSON for each, so Python and numpy start once (about 1.5 s on the Move)
 * for however many presets an update leaves stale. It runs at the lowest priority on cores 0-2 like the engine's own workers (rt.h: core 3 is the
 * SPI callback's), where the real-time render threads always go first, and is closed again when
 * nothing has been asked of it for a while. No audio thread waits on it: a request is queued and its answer arrives on this class's own thread.
 *
 * Linux only (the plugin is): posix_spawn and pipes. */
#pragma once

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <cstring>
#include <deque>
#include <functional>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include <fcntl.h>
#include <pthread.h>
#include <sched.h>
#include <poll.h>
#include <signal.h>
#include <spawn.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;

namespace tb {

class Reconverter {
public:
    struct Result {
        std::string path;
        std::string status;        /* "updated", "same", "current", "no-source", "error", or "failed" (the helper did not answer) */
    };
    using Done = std::function<void(const Result &)>;
    /* An asker's lifetime: `done` runs under its lock and only while `alive`, and the asker clears `alive` under the same lock before it goes, so an answer
     * never reaches a slot that is being destroyed. Keep `done` short (post the work elsewhere). */
    struct Token {
        std::mutex m;
        bool alive = true;
        void end() { std::lock_guard<std::mutex> lk(m); alive = false; }
    };

    /* The process-wide one: every slot's instance shares it (one helper process, one queue). Null when this Move cannot convert (no Python, no converter). */
    static std::shared_ptr<Reconverter> shared(const std::string &converter_dir)
    {
        static std::mutex m;
        static std::weak_ptr<Reconverter> one;
        std::lock_guard<std::mutex> lk(m);
        if (auto p = one.lock()) return p;
        const std::string script = converter_dir + "/reconvert.py";
        struct stat st;
        if (::stat(kPython, &st) != 0 || ::stat(script.c_str(), &st) != 0) return nullptr;
        auto p = std::shared_ptr<Reconverter>(new Reconverter(script));
        one = p;
        return p;
    }

    /* Queue a preset file. Urgent ones (the preset just chosen) go before the background ones. A path already queued is not queued twice: the asker is added
     * to it (each slot hears of the answer) and an urgent ask moves it to the front. `done` runs on this class's thread; `alive` is the asker's token:
     * once it reads false the answer is dropped (the slot has gone). */
    void request(const std::string &path, bool urgent, std::shared_ptr<Token> alive, Done done)
    {
        {
            std::lock_guard<std::mutex> lk(mu_);
            if (stop_) return;
            for (auto it = q_.begin(); it != q_.end(); ++it) {
                if (it->path != path) continue;
                it->who.push_back({ std::move(alive), std::move(done) });
                if (urgent && it != q_.begin()) {
                    Job j = std::move(*it);
                    q_.erase(it);
                    q_.push_front(std::move(j));
                }
                cv_.notify_one();
                return;
            }
            Job j;
            j.path = path;
            j.who.push_back({ std::move(alive), std::move(done) });
            if (urgent) q_.push_front(std::move(j)); else q_.push_back(std::move(j));
        }
        cv_.notify_one();
    }

    /* The path being converted now ("" when idle), for the header's "Upgrading" line. */
    std::string current() const { std::lock_guard<std::mutex> lk(mu_); return cur_; }

    ~Reconverter()
    {
        { std::lock_guard<std::mutex> lk(mu_); stop_ = true; q_.clear(); }
        cv_.notify_one();
        const pid_t pid = pid_.load();
        if (pid > 0) ::kill(pid, SIGKILL);              /* a conversion under way is not waited for: its read ends, and the thread sees stop_ */
        if (th_.joinable()) th_.join();
        closeChild();
    }

private:
    static constexpr const char *kPython = "/usr/bin/python3";
    static constexpr int kIdleCloseMs = 30000;      /* the helper is closed after this long with nothing to do */
    static constexpr int kReadyMs = 30000;          /* how long Python and numpy may take to start */
    static constexpr int kAnswerMs = 120000;        /* the slowest preset (a long sample set) is a few seconds on the Move: this is only a guard against a hang */

    struct Asker {
        std::shared_ptr<Token> alive;
        Done done;
    };
    struct Job {
        std::string path;
        std::vector<Asker> who;
    };

    explicit Reconverter(std::string script) : script_(std::move(script)) { th_ = std::thread([this] { run(); }); }

    void run()
    {
        sigset_t pipe;                                  /* this thread's own: the host's handling of SIGPIPE is left as it is */
        sigemptyset(&pipe);
        sigaddset(&pipe, SIGPIPE);
        pthread_sigmask(SIG_BLOCK, &pipe, nullptr);
        sched_param sp{};
        pthread_setschedparam(pthread_self(), SCHED_OTHER, &sp);     /* never real-time, whichever thread made it */
        if (sysconf(_SC_NPROCESSORS_CONF) >= 4) {                     /* and off core 3, the SPI callback's (rt.h); the helper inherits both */
            cpu_set_t set;
            CPU_ZERO(&set);
            CPU_SET(0, &set); CPU_SET(1, &set); CPU_SET(2, &set);
            pthread_setaffinity_np(pthread_self(), sizeof set, &set);
        }
        for (;;) {
            Job j;
            {
                std::unique_lock<std::mutex> lk(mu_);
                if (!cv_.wait_for(lk, std::chrono::milliseconds(kIdleCloseMs), [this] { return stop_ || !q_.empty(); })) {
                    lk.unlock();
                    closeChild();                       /* idle: let Python go */
                    continue;
                }
                if (stop_) return;
                j = std::move(q_.front());
                q_.pop_front();
                cur_ = j.path;
            }
            Result r{ j.path, "failed" };
            std::string line;
            if (ensureChild() && writeLine(j.path) && readLine(line, kAnswerMs)) r.status = field(line, "status");
            else closeChild();                          /* a helper that did not answer is not asked again: the next job starts a fresh one */
            {
                std::lock_guard<std::mutex> lk(mu_);
                cur_.clear();
            }
            for (auto &a : j.who) {
                if (!a.done || !a.alive) continue;
                std::lock_guard<std::mutex> lk(a.alive->m);
                if (a.alive->alive) a.done(r);
            }
        }
    }

    bool ensureChild()
    {
        if (pid_ > 0) return true;
        int in[2], out[2];
        if (::pipe(in) != 0) return false;
        if (::pipe(out) != 0) { ::close(in[0]); ::close(in[1]); return false; }
        posix_spawn_file_actions_t fa;
        posix_spawn_file_actions_init(&fa);
        posix_spawn_file_actions_adddup2(&fa, in[0], 0);
        posix_spawn_file_actions_adddup2(&fa, out[1], 1);
        posix_spawn_file_actions_addopen(&fa, 2, "/dev/null", O_WRONLY, 0);
        posix_spawn_file_actions_addclose(&fa, in[1]);
        posix_spawn_file_actions_addclose(&fa, out[0]);
        posix_spawn_file_actions_addclosefrom_np(&fa, 3);         /* none of the host's files (glibc 2.34+) */
        posix_spawnattr_t at;                                     /* a plain process: normal scheduling, no blocked signals, its own process group */
        posix_spawnattr_init(&at);
        sigset_t none;
        sigemptyset(&none);
        posix_spawnattr_setsigmask(&at, &none);
        sigset_t dfl;
        sigemptyset(&dfl);
        sigaddset(&dfl, SIGPIPE);
        posix_spawnattr_setsigdefault(&at, &dfl);
        sched_param sp{};
        posix_spawnattr_setschedpolicy(&at, SCHED_OTHER);
        posix_spawnattr_setschedparam(&at, &sp);
        posix_spawnattr_setpgroup(&at, 0);
        posix_spawnattr_setflags(&at, POSIX_SPAWN_SETSIGMASK | POSIX_SPAWN_SETSIGDEF | POSIX_SPAWN_SETSCHEDULER | POSIX_SPAWN_SETPGROUP);
        const char *argv[] = { kPython, "-B", script_.c_str(), "--serve", nullptr };
        pid_t pid = -1;
        const int rc = posix_spawn(&pid, kPython, &fa, &at, const_cast<char **>(argv), environ);
        posix_spawnattr_destroy(&at);
        posix_spawn_file_actions_destroy(&fa);
        ::close(in[0]);
        ::close(out[1]);
        if (rc != 0) { ::close(in[1]); ::close(out[0]); return false; }
        pid_ = pid; wfd_ = in[1]; rfd_ = out[0]; rbuf_.clear();
        ::fcntl(wfd_, F_SETFD, FD_CLOEXEC);
        ::fcntl(rfd_, F_SETFD, FD_CLOEXEC);
        std::string ready;
        if (!readLine(ready, kReadyMs) || ready.find("\"ready\"") == std::string::npos) { closeChild(); return false; }
        return true;
    }

    void closeChild()
    {
        if (wfd_ >= 0) { ::close(wfd_); wfd_ = -1; }    /* stdin closed: the helper's loop ends and it exits */
        if (rfd_ >= 0) { ::close(rfd_); rfd_ = -1; }
        if (pid_ > 0) {
            int st = 0;
            for (int i = 0; i < 50 && ::waitpid(pid_, &st, WNOHANG) == 0; i++) ::usleep(20000);
            if (::waitpid(pid_, &st, WNOHANG) == 0) { ::kill(pid_, SIGKILL); ::waitpid(pid_, &st, 0); }
            pid_ = -1;
        }
    }

    bool writeLine(const std::string &s)
    {
        const std::string l = s + "\n";
        size_t off = 0;
        while (off < l.size()) {                        /* SIGPIPE is blocked on this thread (run): a helper that died is a failed write, not the end of the host */
            const ssize_t n = ::write(wfd_, l.data() + off, l.size() - off);
            if (n <= 0) return false;
            off += (size_t) n;
        }
        return true;
    }

    bool readLine(std::string &line, int timeout_ms)
    {
        const auto end = std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout_ms);
        for (;;) {
            const size_t nl = rbuf_.find('\n');
            if (nl != std::string::npos) { line = rbuf_.substr(0, nl); rbuf_.erase(0, nl + 1); return true; }
            const int left = (int) std::chrono::duration_cast<std::chrono::milliseconds>(end - std::chrono::steady_clock::now()).count();
            if (left <= 0) return false;
            pollfd p{ rfd_, POLLIN, 0 };
            if (::poll(&p, 1, left) <= 0) return false;
            char b[4096];
            const ssize_t n = ::read(rfd_, b, sizeof b);
            if (n <= 0) return false;
            rbuf_.append(b, (size_t) n);
            if (rbuf_.size() > (1u << 20)) return false;
        }
    }

    /* A string field of a one-line JSON object ("status": "updated"): enough for the helper's own answers. */
    static std::string field(const std::string &js, const char *key)
    {
        const std::string k = std::string("\"") + key + "\"";
        size_t i = js.find(k);
        if (i == std::string::npos) return "";
        i = js.find('"', js.find(':', i + k.size()));
        if (i == std::string::npos) return "";
        const size_t e = js.find('"', i + 1);
        return e == std::string::npos ? "" : js.substr(i + 1, e - i - 1);
    }

    std::string script_;
    mutable std::mutex mu_;
    std::condition_variable cv_;
    std::deque<Job> q_;
    std::string cur_;
    bool stop_ = false;
    std::thread th_;
    std::atomic<pid_t> pid_ { -1 };
    int wfd_ = -1, rfd_ = -1;
    std::string rbuf_;
};

} /* namespace tb */
