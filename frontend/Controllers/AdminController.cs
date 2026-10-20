using System.Web.Mvc;
using MvcSmartCctv.Models;
using MvcSmartCctv.Services;

namespace MvcSmartCctv.Controllers
{
    public class AdminController : BaseController
    {
        public ActionResult Index()
        {
            var u = CurrentUser;
            if (!u.IsAdmin) return RedirectToAction("Index", "Home");

            ViewBag.Title = "Admin Dashboard";
            ViewBag.Shell = new ShellViewModel { Active = "admin", UserName = u.DisplayName, RoleLabel = "Admin", IsAdmin = true };

            var users = FastApiClient.GetUsers(u.AccessToken);
            return View(users);
        }

        [HttpPost]
        public ActionResult AddUser(string username, string firstName, string lastName, string email, string role, string password)
        {
            if (!CurrentUser.IsAdmin) return RedirectToAction("Index", "Home");

            if (string.IsNullOrWhiteSpace(username))
            {
                TempData["AddUserError"] = "Username wajib diisi.";
                return RedirectToAction("Index");
            }

            if (string.IsNullOrWhiteSpace(password))
            {
                TempData["AddUserError"] = "Password awal wajib diisi.";
                return RedirectToAction("Index");
            }

            bool ok = FastApiClient.CreateUser(username, firstName, lastName, email, role, password, CurrentUser.AccessToken);
            if (!ok)
            {
                TempData["AddUserError"] = "Gagal membuat user. Username mungkin sudah digunakan.";
            }
            return RedirectToAction("Index");
        }

        [HttpPost]
        public ActionResult ToggleUser(string id)
        {
            if (!CurrentUser.IsAdmin) return RedirectToAction("Index", "Home");

            if (id != CurrentUser.Id)
            {
                FastApiClient.ToggleUser(id, CurrentUser.AccessToken);
            }
            return RedirectToAction("Index");
        }
    }
}
