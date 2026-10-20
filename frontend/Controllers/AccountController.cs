using System.Web.Mvc;
using MvcSmartCctv.Models;
using MvcSmartCctv.Services;

namespace MvcSmartCctv.Controllers
{
    public class AccountController : Controller
    {
        [HttpGet]
        public ActionResult Login(string returnUrl)
        {
            if (Session[SessionKeys.User] != null) return RedirectToAction("Index", "Home");
            ViewBag.ReturnUrl = returnUrl;
            return View();
        }

        [HttpPost]
        public ActionResult Login(string username, string password, string returnUrl)
        {
            var user = FastApiClient.Login(username, password);
            if (user == null)
            {
                ViewBag.ReturnUrl = returnUrl;
                ViewBag.Username = username;
                ViewBag.Error = "Username atau password salah.";
                return View();
            }

            Session[SessionKeys.User] = user;

            if (!string.IsNullOrEmpty(returnUrl) && Url.IsLocalUrl(returnUrl))
                return Redirect(returnUrl);
            return RedirectToAction("Index", "Home");
        }

        public ActionResult Logout()
        {
            Session.Clear();
            Session.Abandon();
            return RedirectToAction("Login");
        }
    }
}
