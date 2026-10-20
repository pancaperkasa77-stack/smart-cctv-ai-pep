using System;
using System.Net.Http;
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

        [HttpGet]
        public Task<ActionResult> Frame(int id, string mode = "apd")
        {
            return Proxy(id, mode, true);
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
