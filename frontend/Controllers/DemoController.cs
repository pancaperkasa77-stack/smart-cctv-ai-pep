using System;
using System.Net.Http;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Web;
using System.Web.Mvc;
using System.Web.SessionState;

namespace MvcSmartCctv.Controllers
{
    [SessionState(SessionStateBehavior.ReadOnly)]
    public class DemoController : BaseController
    {
        private static readonly HttpClient Client = new HttpClient(new HttpClientHandler { UseProxy = false })
        {
            Timeout = TimeSpan.FromSeconds(10)
        };

        private static readonly HttpClient StreamClient = new HttpClient(new HttpClientHandler
        {
            UseProxy = false, AllowAutoRedirect = false, UseCookies = false
        }) { Timeout = TimeSpan.FromSeconds(20) };
        private static readonly Regex StreamFile = new Regex(@"\A[A-Za-z0-9_-][A-Za-z0-9_.-]{0,180}\.(m3u8|ts|mp4|m4s)\z", RegexOptions.CultureInvariant);
        private static readonly Regex ManifestUri = new Regex("URI=\"([^\"]*)\"", RegexOptions.CultureInvariant);

        protected override void OnActionExecuting(ActionExecutingContext context)
        {
            if (CurrentUser == null && context.ActionDescriptor.ActionName == "Stream")
            {
                context.Result = new HttpStatusCodeResult(401, "Login required");
                return;
            }
            base.OnActionExecuting(context);
        }

        private string StreamLink(int id, string file)
        {
            // Only MediaMTX's UUID session query is allowed; never forward arbitrary URLs.
            var parts = file.Split('?');
            if (parts.Length > 2 || !StreamFile.IsMatch(parts[0]) || parts[0].Contains("..")) throw new InvalidDataException();
            string session = null;
            if (parts.Length == 2)
            {
                if (!parts[1].StartsWith("session=", StringComparison.Ordinal)) throw new InvalidDataException();
                session = parts[1].Substring(8);
                if (!ValidStreamSession(session)) throw new InvalidDataException();
            }
            return Url.Action("Stream", "Demo", new { id = id, file = parts[0], session = session });
        }

        private static bool ValidStreamSession(string session)
        {
            Guid parsed;
            return session != null && session.Length == 36 && Guid.TryParseExact(session, "D", out parsed);
        }

        [HttpGet]
        public async Task<ActionResult> Stream(int id, string file = "index.m3u8", string session = null)
        {
            Response.Cache.SetCacheability(HttpCacheability.Private);
            Response.Cache.SetNoStore();
            Response.AddHeader("X-Content-Type-Options", "nosniff");
            if (id != 1 && id != 2) return HttpNotFound();
            if (string.IsNullOrEmpty(file) || !StreamFile.IsMatch(file) || file.Contains("..") || (session != null && !ValidStreamSession(session)))
                return new HttpStatusCodeResult(400);
            Uri origin;
            if (!Uri.TryCreate(Environment.GetEnvironmentVariable("RAW_STREAM_BASE_URL"), UriKind.Absolute, out origin)
                || (origin.Scheme != "http" && origin.Scheme != "https") || !string.IsNullOrEmpty(origin.UserInfo)
                || origin.AbsolutePath != "/" || !string.IsNullOrEmpty(origin.Query) || !string.IsNullOrEmpty(origin.Fragment))
                return new HttpStatusCodeResult(503, "Video unavailable");
            // HLS sources can be forwarded directly: avoid a second HLS demux/remux
            // session. RTSP sources still use the internal browser-compatible relay.
            Uri source;
            var configuredSource = Environment.GetEnvironmentVariable("CCTV_" + id + "_URL");
            var direct = Uri.TryCreate(configuredSource, UriKind.Absolute, out source)
                && source.Scheme == "https" && source.AbsolutePath.EndsWith(".m3u8", StringComparison.OrdinalIgnoreCase)
                && string.IsNullOrEmpty(source.UserInfo) && string.IsNullOrEmpty(source.Query)
                && string.IsNullOrEmpty(source.Fragment);
            Uri upstreamUri;
            if (direct)
            {
                if (session != null) return new HttpStatusCodeResult(400);
                upstreamUri = file == "index.m3u8" ? source : new Uri(source, file);
            }
            else
                upstreamUri = new Uri(origin, "cam" + id + "/" + file + (session == null ? (file == "index.m3u8" ? "?cookieCheck=1" : "") : "?session=" + Uri.EscapeDataString(session)));
            var manifest = file.EndsWith(".m3u8", StringComparison.Ordinal);
            var limit = manifest ? 1024 * 1024 : 16 * 1024 * 1024;
            // Limit applies only to the configured raw origin, not inference or other HTTP clients.
            System.Net.ServicePointManager.FindServicePoint(upstreamUri).ConnectionLimit = 32;
            var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(20));
            HttpResponseMessage result = null;
            System.IO.Stream input = null;
            var handedOff = false;
            try
            {
                result = await StreamClient.GetAsync(upstreamUri, HttpCompletionOption.ResponseHeadersRead, timeout.Token);
                {
                    if (!result.IsSuccessStatusCode)
                    {
                        Console.Error.WriteLine("Raw relay HTTP status: " + (int)result.StatusCode);
                        return new HttpStatusCodeResult(result.StatusCode == System.Net.HttpStatusCode.NotFound ? 404 : 502, "Video unavailable");
                    }
                    if (result.Content.Headers.ContentLength > limit) return new HttpStatusCodeResult(502, "Video response too large");
                    input = await result.Content.ReadAsStreamAsync();
                    if (!manifest)
                    {
                        var streamed = new BoundedStreamResult(result, input, timeout, limit,
                            file.EndsWith(".ts", StringComparison.Ordinal) ? "video/mp2t" : "video/mp4");
                        handedOff = true;
                        return streamed;
                    }
                    using (var output = new MemoryStream())
                    {
                        var buffer = new byte[32768];
                        int count;
                        while ((count = await input.ReadAsync(buffer, 0, buffer.Length, timeout.Token)) > 0)
                        {
                            if (output.Length + count > limit) return new HttpStatusCodeResult(502, "Video response too large");
                            output.Write(buffer, 0, count);
                        }
                        var contentType = result.Content.Headers.ContentType;
                        var text = new UTF8Encoding(false, true).GetString(output.ToArray()).TrimStart('﻿');
                        if (!text.StartsWith("#EXTM3U", StringComparison.Ordinal)) throw new InvalidDataException();
                        var rewritten = new StringBuilder();
                        using (var reader = new StringReader(text))
                        {
                            string line;
                            while ((line = reader.ReadLine()) != null)
                            {
                                line = line.Trim();
                                if (line.StartsWith("#", StringComparison.Ordinal))
                                    line = ManifestUri.Replace(line, match => "URI=\"" + StreamLink(id, match.Groups[1].Value) + "\"");
                                else if (line.Length > 0) line = StreamLink(id, line);
                                rewritten.AppendLine(line);
                            }
                        }
                        return Content(rewritten.ToString(), contentType == null ? "application/vnd.apple.mpegurl" : contentType.ToString(), Encoding.UTF8);
                    }
                }
            }
            catch (TaskCanceledException) { return new HttpStatusCodeResult(504, "Video timeout"); }
            catch (OperationCanceledException) { return new HttpStatusCodeResult(504, "Video timeout"); }
            catch (HttpRequestException) { return new HttpStatusCodeResult(502, "Video unavailable"); }
            catch (InvalidDataException) { return new HttpStatusCodeResult(502, "Video unavailable"); }
            catch (IOException) { return new HttpStatusCodeResult(502, "Video unavailable"); }
            catch (DecoderFallbackException) { return new HttpStatusCodeResult(502, "Video unavailable"); }
            finally
            {
                if (!handedOff)
                {
                    try { if (input != null) input.Dispose(); }
                    finally
                    {
                        try { if (result != null) result.Dispose(); }
                        finally { timeout.Dispose(); }
                    }
                }
            }
        }

        // MVC executes results after the action returns: this result owns the upstream
        // response, body and deadline until the last copy (including disconnects).
        private sealed class BoundedStreamResult : ActionResult
        {
            private readonly HttpResponseMessage upstream;
            private readonly System.IO.Stream input;
            private readonly CancellationTokenSource timeout;
            private readonly int limit;
            private readonly string fallbackType;

            public BoundedStreamResult(HttpResponseMessage upstream, System.IO.Stream input,
                CancellationTokenSource timeout, int limit, string fallbackType)
            {
                this.upstream = upstream;
                this.input = input;
                this.timeout = timeout;
                this.limit = limit;
                this.fallbackType = fallbackType;
            }

            public override void ExecuteResult(ControllerContext context)
            {
                var response = context.HttpContext.Response;
                var started = false;
                // Some Mono network streams do not honor ReadAsync cancellation.
                // Closing the body at the deadline also interrupts a pending read.
                using (timeout)
                using (upstream)
                using (input)
                using (timeout.Token.Register(() =>
                {
                    try { input.Dispose(); }
                    catch (IOException) { }
                    catch (ObjectDisposedException) { }
                }))
                {
                    try
                    {
                        response.BufferOutput = false;
                        response.Cache.SetCacheability(HttpCacheability.Private);
                        response.Cache.SetNoStore();
                        response.ContentType = upstream.Content.Headers.ContentType == null
                            ? fallbackType : upstream.Content.Headers.ContentType.ToString();
                        var length = upstream.Content.Headers.ContentLength;
                        if (length.HasValue)
                            response.AddHeader("Content-Length", length.Value.ToString(System.Globalization.CultureInfo.InvariantCulture));

                        var buffer = new byte[32768];
                        long copied = 0;
                        while (true)
                        {
                            timeout.Token.ThrowIfCancellationRequested();
                            if (!response.IsClientConnected) return;
                            // Read one byte beyond the cap to detect oversized chunked bodies,
                            // but never write that byte to the client.
                            var count = input.ReadAsync(buffer, 0, (int)Math.Min(buffer.Length, limit - copied + 1),
                                timeout.Token).GetAwaiter().GetResult();
                            timeout.Token.ThrowIfCancellationRequested();
                            if (count == 0)
                            {
                                if (length.HasValue && copied != length.Value) throw new IOException();
                                break;
                            }
                            if (copied + count > limit || (length.HasValue && copied + count > length.Value))
                                throw new InvalidDataException();
                            started = true; // Even a failed write may have committed headers.
                            response.OutputStream.Write(buffer, 0, count);
                            response.Flush();
                            copied += count;
                        }
                    }
                    catch (OperationCanceledException) { Fail(response, started, 504); }
                    catch (IOException) { Fail(response, started, timeout.IsCancellationRequested ? 504 : 502); }
                    catch (ObjectDisposedException) { Fail(response, started, timeout.IsCancellationRequested ? 504 : 502); }
                    catch (HttpRequestException) { Fail(response, started, 502); }
                    catch (HttpException) { Fail(response, started, 502); }
                }
            }

            private static void Fail(HttpResponseBase response, bool started, int status)
            {
                if (started || !response.IsClientConnected)
                {
                    // Never replace status/headers or append an error page to a partial segment.
                    try { response.Close(); }
                    catch (HttpException) { }
                    return;
                }
                response.Headers.Remove("Content-Length");
                response.StatusCode = status;
                response.SuppressContent = true;
                response.TrySkipIisCustomErrors = true;
            }
        }

        [HttpGet]
        public Task<ActionResult> Frame(int id, string mode = "apd")
        {
            return Proxy(id, mode, true);
        }

        [HttpGet]
        public ActionResult Playback()
        {
            Response.Cache.SetCacheability(HttpCacheability.Private);
            Response.Cache.SetNoStore();
            try { return Json(MvcSmartCctv.Services.PlaybackApiClient.Get(CurrentUser.AccessToken), JsonRequestBehavior.AllowGet); }
            catch (MvcSmartCctv.Services.PlaybackApiException) { return new HttpStatusCodeResult(503, "Playback settings unavailable"); }
        }

        [HttpGet]
        public Task<ActionResult> Status(int id)
        {
            return Proxy(id, "apd", false);
        }

        private async Task<ActionResult> Proxy(int id, string mode, bool frame)
        {
            Response.Cache.SetCacheability(HttpCacheability.Private);
            Response.Cache.SetNoStore();
            Response.AddHeader("X-Content-Type-Options", "nosniff");
            if (id != 1 && id != 2) return HttpNotFound();
            if (mode != "apd" && mode != "vehicle") return new HttpStatusCodeResult(400);
            var origin = Environment.GetEnvironmentVariable("DEMO_LIVE_BASE_URL");
            if (string.IsNullOrEmpty(origin)) return new HttpStatusCodeResult(503, "Demo inference unavailable");
            var path = "/cameras/" + id + (frame ? "/frame?mode=" + mode : "/status");
            try
            {
                using (var result = await Client.GetAsync(origin.TrimEnd('/') + path))
                {
                    if (!result.IsSuccessStatusCode)
                        return new HttpStatusCodeResult((int)result.StatusCode, "Stream belum siap atau tidak tersedia");
                    if (!frame) return Content(await result.Content.ReadAsStringAsync(), "application/json");
                    foreach (var name in new[] { "X-Frame-Sequence", "X-Frame-Timestamp" })
                        if (result.Headers.Contains(name))
                            Response.AddHeader(name, string.Join(",", result.Headers.GetValues(name)));
                    return File(await result.Content.ReadAsByteArrayAsync(), "image/jpeg");
                }
            }
            catch (HttpRequestException) { return new HttpStatusCodeResult(503, "Demo inference unavailable"); }
            catch (TaskCanceledException) { return new HttpStatusCodeResult(504, "Demo inference timeout"); }
        }
    }
}
