using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Runtime.InteropServices;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows.Forms;

namespace PlanningDDVOutlookBridge
{
    internal static class Program
    {
        private const string Version = "1.0.0";
        private const string DefaultBaseUrl = "https://planning-ddv-usuarios-prueba.onrender.com";
        private const string ContentIdProperty = "http://schemas.microsoft.com/mapi/proptag/0x3712001F";

        private static string InstallDir
        {
            get
            {
                return Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    "PlanningDDVOutlookConnector"
                );
            }
        }

        private static string LogPath
        {
            get { return Path.Combine(InstallDir, "bridge.log"); }
        }

        [STAThread]
        private static int Main(string[] args)
        {
            ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12;

            try
            {
                if (args.Length == 0 || string.IsNullOrWhiteSpace(args[0]))
                {
                    throw new InvalidOperationException("No se recibio URL planningddv://.");
                }

                Directory.CreateDirectory(InstallDir);
                WriteLog("Inicio", "Version " + Version + " - URL recibida.");

                Uri protocolUri = new Uri(args[0]);
                string id = QueryValue(protocolUri, "id");
                if (string.IsNullOrWhiteSpace(id))
                {
                    id = QueryValue(protocolUri, "token");
                }
                if (string.IsNullOrWhiteSpace(id))
                {
                    throw new InvalidOperationException("Identificador de borrador vacio.");
                }

                string packageUrl = QueryValue(protocolUri, "package_url");
                if (string.IsNullOrWhiteSpace(packageUrl))
                {
                    packageUrl = BaseUrl().TrimEnd('/') + "/api/mail/draft/" + Uri.EscapeDataString(id);
                }

                Dictionary<string, object> package = DownloadPackage(packageUrl);
                CreateOutlookDraft(package);
                WriteLog("Outlook", "Borrador creado correctamente.");
                return 0;
            }
            catch (Exception ex)
            {
                WriteLog("Error", ex.ToString());
                MessageBox.Show(
                    ex.Message + Environment.NewLine + Environment.NewLine +
                    "Este puente requiere Outlook clasico de escritorio configurado en Windows. El Nuevo Outlook no expone automatizacion COM compatible." +
                    Environment.NewLine + Environment.NewLine + "Log: " + LogPath,
                    "Planning DDV Outlook Bridge",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error
                );
                return 1;
            }
        }

        private static Dictionary<string, object> DownloadPackage(string packageUrl)
        {
            WriteLog("Paquete", packageUrl);
            using (WebClient client = new WebClient())
            {
                client.Encoding = Encoding.UTF8;
                client.Headers[HttpRequestHeader.Accept] = "application/json";
                string raw = client.DownloadString(packageUrl);
                JavaScriptSerializer serializer = new JavaScriptSerializer();
                serializer.MaxJsonLength = int.MaxValue;
                return serializer.Deserialize<Dictionary<string, object>>(raw);
            }
        }

        private static void CreateOutlookDraft(Dictionary<string, object> package)
        {
            string to = RequiredString(package, "to");
            string subject = RequiredString(package, "subject");
            string htmlBody = RequiredString(package, "html_body");
            string cc = OptionalString(package, "cc");

            Type outlookType = Type.GetTypeFromProgID("Outlook.Application");
            if (outlookType == null)
            {
                throw new InvalidOperationException("No se encontro Outlook clasico de escritorio. El Nuevo Outlook no expone automatizacion COM compatible.");
            }

            object outlook = Activator.CreateInstance(outlookType);
            dynamic app = outlook;
            dynamic mail = app.CreateItem(0);
            mail.To = to;
            mail.CC = cc;
            mail.Subject = subject;

            string tempDir = Path.Combine(Path.GetTempPath(), "planningddv_mail_" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(tempDir);

            try
            {
                foreach (Dictionary<string, object> attachment in Attachments(package))
                {
                    string filePath = WriteAttachment(tempDir, attachment);
                    dynamic item = mail.Attachments.Add(filePath);
                    bool inline = BoolValue(attachment, "inline");
                    string cid = OptionalString(attachment, "cid");
                    if (inline && !string.IsNullOrWhiteSpace(cid))
                    {
                        item.PropertyAccessor.SetProperty(ContentIdProperty, cid);
                    }
                }

                mail.Display();
                string signature = Convert.ToString(mail.HTMLBody);
                mail.HTMLBody = htmlBody + "<br>" + signature;
            }
            finally
            {
                try
                {
                    Directory.Delete(tempDir, true);
                }
                catch
                {
                    // Outlook normally copies attachments immediately. Keep failures non-blocking.
                }
            }
        }

        private static IEnumerable<Dictionary<string, object>> Attachments(Dictionary<string, object> package)
        {
            if (!package.ContainsKey("attachments") || package["attachments"] == null)
            {
                yield break;
            }

            ArrayList list = package["attachments"] as ArrayList;
            if (list == null)
            {
                yield break;
            }

            foreach (object item in list)
            {
                Dictionary<string, object> attachment = item as Dictionary<string, object>;
                if (attachment != null)
                {
                    yield return attachment;
                }
            }
        }

        private static string WriteAttachment(string tempDir, Dictionary<string, object> attachment)
        {
            string safeName = Path.GetFileName(OptionalString(attachment, "name"));
            if (string.IsNullOrWhiteSpace(safeName))
            {
                safeName = Guid.NewGuid().ToString("N");
            }

            string content = RequiredString(attachment, "content_base64");
            string path = Path.Combine(tempDir, safeName);
            File.WriteAllBytes(path, Convert.FromBase64String(content));
            WriteLog("Adjunto", path);
            return path;
        }

        private static string BaseUrl()
        {
            string fromEnv = Environment.GetEnvironmentVariable("PLANNING_DDV_BASE_URL");
            if (!string.IsNullOrWhiteSpace(fromEnv))
            {
                return fromEnv.Trim();
            }

            string path = Path.Combine(InstallDir, "base_url.txt");
            if (File.Exists(path))
            {
                string value = File.ReadAllText(path, Encoding.UTF8).Trim();
                if (!string.IsNullOrWhiteSpace(value))
                {
                    return value;
                }
            }

            return DefaultBaseUrl;
        }

        private static string QueryValue(Uri uri, string name)
        {
            string query = uri.Query;
            if (query.StartsWith("?"))
            {
                query = query.Substring(1);
            }

            foreach (string part in query.Split(new[] { '&' }, StringSplitOptions.RemoveEmptyEntries))
            {
                string[] pieces = part.Split(new[] { '=' }, 2);
                string key = Uri.UnescapeDataString(pieces[0]);
                if (string.Equals(key, name, StringComparison.OrdinalIgnoreCase))
                {
                    return pieces.Length > 1 ? Uri.UnescapeDataString(pieces[1].Replace("+", " ")) : "";
                }
            }
            return "";
        }

        private static string RequiredString(Dictionary<string, object> data, string key)
        {
            string value = OptionalString(data, key);
            if (string.IsNullOrWhiteSpace(value))
            {
                throw new InvalidOperationException("Falta el campo requerido del paquete: " + key);
            }
            return value;
        }

        private static string OptionalString(Dictionary<string, object> data, string key)
        {
            if (!data.ContainsKey(key) || data[key] == null)
            {
                return "";
            }
            return Convert.ToString(data[key]);
        }

        private static bool BoolValue(Dictionary<string, object> data, string key)
        {
            if (!data.ContainsKey(key) || data[key] == null)
            {
                return false;
            }
            object value = data[key];
            if (value is bool)
            {
                return (bool)value;
            }
            return string.Equals(Convert.ToString(value), "true", StringComparison.OrdinalIgnoreCase) ||
                   string.Equals(Convert.ToString(value), "1", StringComparison.OrdinalIgnoreCase);
        }

        private static void WriteLog(string stage, string message)
        {
            try
            {
                Directory.CreateDirectory(InstallDir);
                File.AppendAllText(
                    LogPath,
                    DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + " [" + stage + "] " + message + Environment.NewLine,
                    Encoding.UTF8
                );
            }
            catch
            {
                // Logging must not block draft creation.
            }
        }
    }
}
