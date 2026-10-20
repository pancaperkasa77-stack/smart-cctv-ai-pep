using System;
using System.Linq;
using System.Web.Mvc;
using MvcSmartCctv.Models;
using MvcSmartCctv.Services;

namespace MvcSmartCctv.Controllers
{
    public class ApdController : BaseController
    {
        public ActionResult Index()
        {
            SetShell("apd");
            ViewBag.Title = "APD Detection";

            var token = CurrentUser.AccessToken;
            var cameras = FastApiClient.GetCameras("apd", token);
            var violations = FastApiClient.GetViolations("apd", 100, token);
            var stats = FastApiClient.GetCategoryStats("apd", token);

            var vm = new DetectionViewModel
            {
                Category = "apd",
                PageTitle = "APD Detection",
                PageDescription = "Deteksi kepatuhan alat pelindung diri (mis. helm) di kamera terkait.",
                AccentColor = "#B57712",
                Cameras = cameras,
                Violations = violations,
                StatCams = cameras.Count,
                StatToday = stats == null ? (int?)null : stats.TotalToday,
                StatOpen = stats == null ? (int?)null : stats.OpenCases,
            };
            return View("~/Views/Shared/Detection.cshtml", vm);
        }
    }
}
