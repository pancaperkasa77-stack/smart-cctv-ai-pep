using System;
using System.Collections.Generic;
using System.Web.Mvc;
using System.Web.SessionState;
using MvcSmartCctv.Models;
using MvcSmartCctv.Services;

namespace MvcSmartCctv.Controllers
{
    // Polling must not acquire the exclusive session lock used by page actions:
    // HLS requests for the same user need to run while these API reads finish.
    [SessionState(SessionStateBehavior.ReadOnly)]
    [OutputCache(NoStore = true, Duration = 0, VaryByParam = "*")]
    public class HistoryController : BaseController
    {
        // MVC's default provider removes TempData from session when loading it,
        // even on GETs. Polls must neither consume messages/drafts nor save them.
        protected override ITempDataProvider CreateTempDataProvider()
        {
            return new NoOpTempDataProvider();
        }

        [HttpGet]
        public ActionResult Detection(string category)
        {
            if (category != "apd" && category != "vehicle")
                return new HttpStatusCodeResult(400, "Invalid category.");

            var token = CurrentUser.AccessToken;
            var cameras = FastApiClient.GetCameras(category, token);
            var violations = FastApiClient.GetViolations(category, 100, token);
            var stats = FastApiClient.GetCategoryStats(category, token);
            var vehicle = category == "vehicle";
            var vm = new DetectionViewModel
            {
                Category = category,
                PageTitle = vehicle ? "Vehicle Violation" : "APD Detection",
                PageDescription = vehicle
                    ? "Deteksi penumpang di bak kendaraan pada zona kamera terkait."
                    : "Deteksi kepatuhan alat pelindung diri (mis. helm) di kamera terkait.",
                AccentColor = vehicle ? "#B8021F" : "#B57712",
                Cameras = cameras,
                Violations = violations,
                StatCams = cameras.Count,
                StatToday = stats == null ? (int?)null : stats.TotalToday,
                StatOpen = stats == null ? (int?)null : stats.OpenCases,
            };
            // PartialView skips _ViewStart/layout; the client imports only history
            // blocks, not player markup or scripts from the shared view.
            return PartialView("~/Views/Shared/Detection.cshtml", vm);
        }

        [HttpGet]
        public ActionResult Cases(string filter)
        {
            if (filter != "apd" && filter != "vehicle" && filter != "open") filter = "all";
            return PartialView("~/Views/Cases/Index.cshtml", new CasesViewModel
            {
                ActiveFilter = filter,
                Rows = FastApiClient.GetCases(filter, CurrentUser.AccessToken),
            });
        }

        protected override void OnException(ExceptionContext filterContext)
        {
            filterContext.ExceptionHandled = true;
            Response.TrySkipIisCustomErrors = true;
            filterContext.Result = new HttpStatusCodeResult(502, "History refresh unavailable.");
        }

        private sealed class NoOpTempDataProvider : ITempDataProvider
        {
            public IDictionary<string, object> LoadTempData(ControllerContext controllerContext)
            {
                return new Dictionary<string, object>();
            }

            public void SaveTempData(ControllerContext controllerContext, IDictionary<string, object> values)
            {
            }
        }
    }
}
