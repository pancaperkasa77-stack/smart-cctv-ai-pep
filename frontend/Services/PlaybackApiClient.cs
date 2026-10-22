using System;
using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Web.Script.Serialization;

namespace MvcSmartCctv.Services
{
    public static class PlaybackApiClient
    {
        private static readonly string BaseUrl = (Environment.GetEnvironmentVariable("FASTAPI_BASE_URL") ?? "http://127.0.0.1:8000").TrimEnd('/');

        public static PlaybackSettings Get(string token)
        {
            return Send(HttpMethod.Get, null, token);
        }

        public static PlaybackSettings Save(string mode, long version, string token)
        {
            if ((mode != "raw" && mode != "ai") || version < 0)
                throw new PlaybackApiException("Mode atau versi pemutaran tidak valid. Muat ulang pengaturan.", 400);
            return Send(HttpMethod.Put, new { mode = mode, version = version }, token);
        }

        private static PlaybackSettings Send(HttpMethod method, object payload, string token)
        {
            try
            {
                using (var client = new HttpClient())
                using (var request = new HttpRequestMessage(method, "settings/playback"))
                {
                    client.BaseAddress = new Uri(BaseUrl + "/api/v1/");
                    client.Timeout = TimeSpan.FromSeconds(10);
                    client.DefaultRequestHeaders.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
                    client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", token);
                    var serializer = new JavaScriptSerializer();
                    if (payload != null)
                        request.Content = new StringContent(serializer.Serialize(payload), Encoding.UTF8, "application/json");
                    using (var response = client.SendAsync(request).GetAwaiter().GetResult())
                    {
                        if (!response.IsSuccessStatusCode)
                        {
                            var status = (int)response.StatusCode;
                            if (response.StatusCode == HttpStatusCode.Unauthorized)
                                throw new PlaybackApiException("Sesi tidak berlaku. Masuk kembali untuk memuat pengaturan pemutaran.", status);
                            if (response.StatusCode == HttpStatusCode.Forbidden)
                                throw new PlaybackApiException("Akses pengaturan pemutaran ditolak. Perubahan memerlukan akun Admin.", status);
                            if (response.StatusCode == HttpStatusCode.Conflict)
                                throw new PlaybackApiException("Pengaturan pemutaran telah diubah pengguna lain. Periksa status terbaru lalu pilih dan simpan kembali.", status);
                            if (response.StatusCode == HttpStatusCode.BadRequest || status == 422)
                                throw new PlaybackApiException("Mode atau versi pemutaran ditolak. Muat ulang pengaturan sebelum mencoba lagi.", status);
                            throw new PlaybackApiException("Layanan pemutaran gagal merespons. Muat ulang status sebelum mencoba lagi; penyimpanan mungkin sudah diterima.", status);
                        }
                        var settings = serializer.Deserialize<PlaybackSettings>(response.Content.ReadAsStringAsync().GetAwaiter().GetResult());
                        if (settings == null || (settings.mode != "raw" && settings.mode != "ai") || settings.version < 0)
                            throw new PlaybackApiException("Respons pengaturan pemutaran tidak valid. Muat ulang status sebelum mencoba lagi.", 0);
                        return settings;
                    }
                }
            }
            catch (PlaybackApiException) { throw; }
            catch (Exception)
            {
                // Never expose response bodies, transport details, or bearer tokens.
                throw new PlaybackApiException("Layanan pemutaran tidak dapat dihubungi. Muat ulang status sebelum mencoba lagi; penyimpanan mungkin sudah diterima.", 0);
            }
        }
    }

    // Wire names match the API. Missing version must not become a valid version 0.
    public class PlaybackSettings
    {
        public PlaybackSettings() { version = -1; }
        public string mode { get; set; }
        public long version { get; set; }
    }

    public class PlaybackApiException : Exception
    {
        public int StatusCode { get; private set; }
        public PlaybackApiException(string message, int statusCode) : base(message) { StatusCode = statusCode; }
    }
}
