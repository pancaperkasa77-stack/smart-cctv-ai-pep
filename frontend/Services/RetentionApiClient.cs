using System;
using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Web.Script.Serialization;
using MvcSmartCctv.Models;

namespace MvcSmartCctv.Services
{
    public static class RetentionApiClient
    {
        private static readonly string BaseUrl = (Environment.GetEnvironmentVariable("FASTAPI_BASE_URL") ?? "http://127.0.0.1:8000").TrimEnd('/');

        public static RetentionSummary Get(string token)
        {
            return Send(HttpMethod.Get, "settings/retention", null, token);
        }

        public static RetentionSummary Preview(bool enabled, int maxRecords, string token)
        {
            return Send(HttpMethod.Post, "settings/retention/preview", new { enabled = enabled, max_records = maxRecords }, token);
        }

        public static RetentionSummary Save(bool enabled, int maxRecords, string previewToken, long version, string token)
        {
            return Send(HttpMethod.Put, "settings/retention", new
            {
                enabled = enabled, max_records = maxRecords, preview_token = previewToken,
                confirmed = true, version = version
            }, token);
        }

        private static RetentionSummary Send(HttpMethod method, string path, object payload, string token)
        {
            try
            {
                using (var client = new HttpClient())
                using (var request = new HttpRequestMessage(method, path))
                {
                    client.BaseAddress = new Uri(BaseUrl + "/api/v1/");
                    client.Timeout = TimeSpan.FromSeconds(30);
                    client.DefaultRequestHeaders.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
                    client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", token);
                    var serializer = new JavaScriptSerializer();
                    if (payload != null)
                        request.Content = new StringContent(serializer.Serialize(payload), Encoding.UTF8, "application/json");
                    using (var response = client.SendAsync(request).GetAwaiter().GetResult())
                    {
                        if (!response.IsSuccessStatusCode)
                        {
                            if (response.StatusCode == HttpStatusCode.Unauthorized || response.StatusCode == HttpStatusCode.Forbidden)
                                throw new RetentionApiException("Akses retensi ditolak. Masuk kembali dengan akun Admin.");
                            if (response.StatusCode == HttpStatusCode.Conflict || response.StatusCode == HttpStatusCode.BadRequest || (int)response.StatusCode == 422 || (int)response.StatusCode == 410)
                                throw new RetentionApiException("Pratinjau tidak berlaku atau pengaturan telah berubah. Periksa nilai dan buat pratinjau baru sebelum menyimpan.");
                            throw new RetentionApiException("Layanan retensi gagal merespons. Muat ulang status sebelum mencoba lagi; penyimpanan mungkin sudah diterima.");
                        }
                        var summary = serializer.Deserialize<RetentionSummary>(response.Content.ReadAsStringAsync().GetAwaiter().GetResult());
                        if (summary == null || summary.max_records < 1 || summary.max_records > 1000000)
                            throw new RetentionApiException("Respons layanan retensi tidak valid. Muat ulang status sebelum mencoba lagi.");
                        return summary;
                    }
                }
            }
            catch (RetentionApiException) { throw; }
            catch (Exception)
            {
                // Never expose response bodies, transport details, or bearer tokens.
                throw new RetentionApiException("Layanan retensi tidak dapat dihubungi. Muat ulang status sebelum mencoba lagi; penyimpanan mungkin sudah diterima.");
            }
        }
    }

    public class RetentionApiException : Exception
    {
        public RetentionApiException(string message) : base(message) { }
    }
}
