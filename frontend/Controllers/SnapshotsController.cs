using System;
using System.Net.Http;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Web;
using System.Web.Mvc;
using System.Web.SessionState;

namespace MvcSmartCctv.Controllers
{
    [SessionState(SessionStateBehavior.ReadOnly)]
    public class SnapshotsController : BaseController
    {
        private static readonly HttpClient Client = new HttpClient(new HttpClientHandler
        {
            UseProxy = false,
            AllowAutoRedirect = false
        })
        {
            Timeout = TimeSpan.FromSeconds(10),
            MaxResponseContentBufferSize = 8 * 1024 * 1024
        };

        [HttpGet]
        public async Task<ActionResult> Image(string filename)
        {
            Response.Cache.SetCacheability(HttpCacheability.Private);
            Response.Cache.SetNoStore();
            Response.AddHeader("X-Content-Type-Options", "nosniff");

            // Only generated JPEG basenames, never paths or user-selected origins.
            if (string.IsNullOrEmpty(filename) || filename.Length > 200 ||
                !Regex.IsMatch(filename, @"\A[A-Za-z0-9_-]+\.(?:jpg|jpeg)\z", RegexOptions.IgnoreCase))
                return new HttpStatusCodeResult(400);

            Uri origin;
            var baseUrl = Environment.GetEnvironmentVariable("FASTAPI_BASE_URL") ?? "http://127.0.0.1:8000";
            if (!Uri.TryCreate(baseUrl, UriKind.Absolute, out origin) ||
                (origin.Scheme != "http" && origin.Scheme != "https") ||
                !string.IsNullOrEmpty(origin.UserInfo) || !string.IsNullOrEmpty(origin.Query) ||
                !string.IsNullOrEmpty(origin.Fragment))
                return new HttpStatusCodeResult(503);

            // Backend static storage needs no bearer token. Session auth is enforced by BaseController.
            var url = origin.AbsoluteUri.TrimEnd('/') + "/snapshots/" + Uri.EscapeDataString(filename);
            try
            {
                using (var result = await Client.GetAsync(url))
                {
                    if (result.StatusCode == System.Net.HttpStatusCode.NotFound) return HttpNotFound();
                    if (!result.IsSuccessStatusCode) return new HttpStatusCodeResult(502);
                    var contentType = result.Content.Headers.ContentType;
                    if (contentType == null || !string.Equals(contentType.MediaType, "image/jpeg", StringComparison.OrdinalIgnoreCase))
                        return new HttpStatusCodeResult(502);
                    var bytes = await result.Content.ReadAsByteArrayAsync();
                    if (bytes.Length < 3 || bytes[0] != 0xff || bytes[1] != 0xd8 || bytes[2] != 0xff)
                        return new HttpStatusCodeResult(502);
                    return File(bytes, "image/jpeg");
                }
            }
            catch (HttpRequestException) { return new HttpStatusCodeResult(503); }
            catch (TaskCanceledException) { return new HttpStatusCodeResult(504); }
        }
    }
}
