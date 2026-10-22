(function () {
    'use strict';
    document.querySelectorAll('.demo-live-camera').forEach(function (card) {
        if (card.dataset.playerInitialized) return;
        card.dataset.playerInitialized = 'true';
        var video = card.querySelector('.demo-video');
        var state = card.querySelector('.demo-video-state');
        var inference = card.querySelector('.demo-state');
        var storage = card.querySelector('.demo-storage');
        var pause = card.querySelector('.demo-pause');
        var hls = null, suspended = false, paused = false, failed = false;
        var transportBroken = false, disposing = false, playerGeneration = 0, lastMediaRecovery = 0;
        var mode = null, modeTimer = null, modeRequest = null;
        var ai = card.querySelector('.demo-ai-frame'), aiTimer = null, aiRequest = null, aiUrl = null;
        var retries = 0, retryTimer = null, healthTimer = null, statusTimer = null;
        var statusRequest = null, lastTime = 0, lastProgress = Date.now(), healthySince = 0;

        function message(text) { state.textContent = (mode === 'ai' ? 'Deteksi AI: ' : 'Video live: ') + text; }
        async function refreshAI() {
            if (suspended || mode !== 'ai') return;
            if (paused || document.hidden) { aiTimer = setTimeout(refreshAI, 1000); return; }
            var controller = new AbortController(); aiRequest = controller;
            var timeout = setTimeout(function () { controller.abort(); }, 12000);
            try {
                var r = await fetch(card.dataset.frameUrl + '&t=' + Date.now(), { credentials: 'same-origin', cache: 'no-store', signal: controller.signal });
                if (!r.ok || !(r.headers.get('Content-Type') || '').startsWith('image/jpeg')) throw new Error('frame');
                var blob = await r.blob();
                if (suspended || paused || mode !== 'ai' || controller.signal.aborted) return;
                var old = aiUrl; aiUrl = URL.createObjectURL(blob); ai.src = aiUrl;
                if (old) URL.revokeObjectURL(old);
                ai.style.opacity = '1'; message('bounding box · ' + new Date().toLocaleTimeString('id-ID'));
            } catch (e) { if (!suspended && !paused && mode === 'ai') { ai.style.opacity = '0.35'; message('menunggu hasil inferensi...'); } }
            finally { clearTimeout(timeout); if (aiRequest === controller) aiRequest = null; if (!suspended && mode === 'ai') aiTimer = setTimeout(refreshAI, 2000); }
        }
        function setMode(next) {
            if (next === mode || (next !== 'raw' && next !== 'ai')) return;
            mode = next; clearTimeout(retryTimer); retryTimer = null; clearTimeout(aiTimer);
            if (aiRequest) aiRequest.abort();
            disposeVideo(); paused = false; failed = false; retries = 0;
            video.style.display = mode === 'raw' ? 'block' : 'none';
            ai.style.display = mode === 'ai' ? 'block' : 'none';
            if (aiUrl) { URL.revokeObjectURL(aiUrl); aiUrl = null; ai.removeAttribute('src'); }
            pause.textContent = 'Jeda tampilan';
            card.querySelector('.demo-buffer').style.display = mode === 'raw' ? 'block' : 'none';
            if (mode === 'raw') startVideo(); else refreshAI();
        }
        async function refreshMode() {
            if (suspended || document.hidden || modeRequest) return;
            var controller = new AbortController(); modeRequest = controller;
            var timeout = setTimeout(function () { controller.abort(); }, 10000);
            try {
                var r = await fetch(card.dataset.playbackUrl, { credentials: 'same-origin', cache: 'no-store', signal: controller.signal });
                if (!r.ok) throw new Error('mode');
                var value = await r.json(); if (!suspended && !controller.signal.aborted) setMode(value.mode);
            } catch (e) { if (mode === null) message('pengaturan tampilan belum tersedia'); }
            finally { clearTimeout(timeout); if (modeRequest === controller) modeRequest = null; }
        }
        function disposeVideo() {
            disposing = true; playerGeneration++;
            if (hls) { hls.destroy(); hls = null; }
            video.removeAttribute('src');
            video.load();
            setTimeout(function () { disposing = false; }, 0);
        }
        function play() {
            var promise = video.play();
            if (promise) promise.catch(function () {
                if (!suspended && !failed && !retryTimer) message('tekan Putar untuk memulai');
            });
        }
        function retry() {
            if (mode !== 'raw' || suspended || retryTimer || failed) return;
            transportBroken = true;
            if (paused) return;
            healthySince = 0;
            retries = Math.min(retries + 1, 5);
            message('koneksi terganggu; menghubungkan ulang otomatis...');
            // Set timer before teardown so pause events cannot become user intent.
            retryTimer = setTimeout(function () { retryTimer = null; startVideo(); }, Math.min(1000 * Math.pow(2, retries), 15000));
            disposeVideo();
        }
        function startVideo() {
            if (mode !== 'raw' || suspended || paused || failed) return;
            lastProgress = Date.now(); lastTime = 0; healthySince = 0;
            message('memuat video RAW...');
            video.muted = true;
            transportBroken = false;
            var generation = ++playerGeneration;
            // Prefer MSE on Chromium; some versions advertise native HLS without
            // reliably loading authenticated playlists. Safari uses native fallback.
            if (!(window.Hls && window.Hls.isSupported()) && video.canPlayType('application/vnd.apple.mpegurl')) {
                video.src = card.dataset.streamUrl;
                play();
            } else if (window.Hls && window.Hls.isSupported()) {
                hls = new window.Hls({
                    lowLatencyMode: false, maxBufferLength: 24, maxMaxBufferLength: 40,
                    backBufferLength: 15, liveSyncDurationCount: 4, liveMaxLatencyDurationCount: 8,
                    manifestLoadingTimeOut: 20000, manifestLoadingMaxRetry: 2,
                    levelLoadingTimeOut: 20000, levelLoadingMaxRetry: 2,
                    fragLoadingTimeOut: 20000, fragLoadingMaxRetry: 2
                });
                var instance = hls;
                hls.on(window.Hls.Events.MANIFEST_PARSED, function () { if (generation === playerGeneration && !paused && !suspended) play(); });
                hls.on(window.Hls.Events.ERROR, function (event, data) {
                    if (generation !== playerGeneration || !data.fatal) return;
                    if (data.response && data.response.code === 401) {
                        failed = true; disposeVideo(); message('sesi berakhir; masuk kembali'); return;
                    }
                    if (!paused && data.type === window.Hls.ErrorTypes.MEDIA_ERROR && Date.now() - lastMediaRecovery > 10000) {
                        lastMediaRecovery = Date.now(); instance.recoverMediaError(); return;
                    }
                    retry();
                });
                hls.loadSource(card.dataset.streamUrl);
                hls.attachMedia(video);
            } else {
                failed = true;
                message('HLS tidak didukung atau pustaka pemutar tidak tersedia.');
                pause.textContent = 'Coba lagi';
            }
        }
        video.addEventListener('playing', function () {
            if (mode !== 'raw' || suspended || failed || retryTimer) return;
            paused = false; lastProgress = Date.now(); healthySince = Date.now();
            pause.textContent = 'Jeda tampilan'; message('berjalan · RAW tanpa anotasi');
        });
        video.addEventListener('pause', function () {
            if (mode !== 'raw' || disposing || transportBroken || suspended || failed || retryTimer || !video.getAttribute('src') && !hls) return;
            paused = true; pause.textContent = 'Lanjutkan tampilan'; message('dijeda');
        });
        video.addEventListener('play', function () { paused = false; lastProgress = Date.now(); });
        ['waiting', 'stalled'].forEach(function (name) {
            video.addEventListener(name, function () {
                if (!suspended && !paused && !failed && !retryTimer) { healthySince = 0; message('menunggu buffer...'); }
            });
        });
        video.addEventListener('error', retry);
        pause.onclick = function () {
            if (mode === 'ai') {
                paused = !paused; if (paused && aiRequest) aiRequest.abort();
                pause.textContent = paused ? 'Lanjutkan tampilan' : 'Jeda tampilan';
                if (paused) message('dijeda');
                return;
            }
            if (failed) { failed = false; paused = false; retries = 0; pause.textContent = 'Jeda tampilan'; startVideo(); }
            else if (paused || video.paused) {
                paused = false;
                if (transportBroken) { disposeVideo(); startVideo(); }
                else if (!hls && !video.getAttribute('src') && !retryTimer) startVideo();
                else {
                    // Resume near live edge rather than replaying an old paused buffer.
                    if (video.seekable.length) video.currentTime = Math.max(video.seekable.start(video.seekable.length - 1), video.seekable.end(video.seekable.length - 1) - 12);
                    play();
                }
            } else { paused = true; video.pause(); }
        };
        card.querySelector('.demo-fullscreen').onclick = function () {
            var target = mode === 'ai' ? ai : video;
            if (target.requestFullscreen) { var promise = target.requestFullscreen(); if (promise) promise.catch(function () {}); }
            else if (video.webkitEnterFullscreen) video.webkitEnterFullscreen();
        };
        function health() {
            if (suspended || mode !== 'raw') return;
            var seconds = 0;
            for (var i = 0; i < video.buffered.length; i++) {
                if (video.buffered.start(i) <= video.currentTime && video.buffered.end(i) >= video.currentTime)
                    seconds = video.buffered.end(i) - video.currentTime;
            }
            card.querySelector('.demo-buffer').textContent = 'Buffer: ' + seconds.toFixed(1) + ' dtk' + (seconds < 1 ? ' · rendah' : ' · tersedia');
            if (paused || failed || retryTimer || document.hidden) return;
            if (video.currentTime !== lastTime) {
                lastTime = video.currentTime; lastProgress = Date.now();
                if (!healthySince) healthySince = Date.now();
                if (Date.now() - healthySince > 60000) retries = 0;
            } else if (Date.now() - lastProgress > 30000) retry();
        }
        async function refreshStatus() {
            if (suspended || document.hidden || statusRequest) return;
            var controller = new AbortController(); statusRequest = controller;
            var timeout = setTimeout(function () { controller.abort(); }, 12000);
            try {
                var response = await fetch(card.dataset.statusUrl, { credentials: 'same-origin', cache: 'no-store', signal: controller.signal });
                if (!response.ok) throw new Error('status');
                var s = await response.json();
                if (suspended || controller.signal.aborted) return;
                card.querySelector('.demo-name').textContent = 'CCTV ' + s.id + ' — ' + s.name;
                card.querySelector('.demo-source').textContent = s.source_attribution + ' (' + s.source_type + ')';
                inference.textContent = 'Inferensi server: ' + (s.inference && s.inference.ready ? 'aktif' : s.inference && s.inference.stale ? 'hasil kedaluwarsa / tertunda' : 'belum siap / tidak tersedia') + ' · tetap berjalan pada kedua mode tampilan';
                var p = s.persistence;
                if (!p) storage.textContent = 'Penyimpanan: status belum tersedia; jangan anggap hasil tersimpan.';
                else if (!p.enabled) storage.textContent = 'Penyimpanan tidak aktif; inferensi bukan bukti tersimpan.';
                else if (p.enqueue_error || p.last_error || p.quarantined > 0)
                    storage.textContent = 'Penyimpanan perlu perhatian: ' + p.pending + ' tertunda (dicoba ulang), ' + p.quarantined + ' dikarantina. Periksa riwayat untuk hasil terkonfirmasi.';
                else if (p.pending > 0) storage.textContent = 'Penyimpanan: ' + p.pending + ' menunggu pengiriman / percobaan ulang.';
                else storage.textContent = 'Penyimpanan aktif; antrean kosong. Pelanggaran tersimpan dapat diperiksa di riwayat.';
            } catch (error) {
                if (!suspended) {
                    inference.textContent = 'Inferensi server: status tidak tersedia';
                    storage.textContent = 'Status penyimpanan tidak tersedia; hasil belum dapat dikonfirmasi tersimpan.';
                }
            } finally { clearTimeout(timeout); if (statusRequest === controller) statusRequest = null; }
        }
        function startTimers() {
            clearInterval(healthTimer); clearInterval(statusTimer);
            healthTimer = setInterval(health, 1000); statusTimer = setInterval(refreshStatus, 15000);
            clearInterval(modeTimer); modeTimer = setInterval(refreshMode, 5000);
            refreshStatus(); refreshMode();
        }
        window.addEventListener('pagehide', function () {
            suspended = true;
            clearTimeout(retryTimer); retryTimer = null;
            clearInterval(healthTimer); clearInterval(statusTimer); clearInterval(modeTimer); clearTimeout(aiTimer);
            if (aiRequest) aiRequest.abort();
            if (modeRequest) modeRequest.abort();
            if (statusRequest) { statusRequest.abort(); statusRequest = null; }
            disposeVideo();
        });
        window.addEventListener('pageshow', function (event) {
            if (event.persisted && suspended) {
                suspended = false; mode = null; modeRequest = null; aiRequest = null; startTimers();
            }
        });
        startTimers();
    });
}());
