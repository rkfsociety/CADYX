using System;
using System.Collections.Concurrent;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices.WindowsRuntime;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using Windows.Devices.Bluetooth;
using Windows.Devices.Bluetooth.Advertisement;
using Windows.Devices.Bluetooth.GenericAttributeProfile;
using Windows.Foundation;
using Windows.Storage.Streams;

internal static class Program
{
    private static readonly Guid ServiceUuid = Guid.Parse("0000FFE0-0000-1000-8000-00805F9B34FB");
    private static readonly Guid CharacteristicUuid = Guid.Parse("0000FFE1-0000-1000-8000-00805F9B34FB");
    private const string DefaultDeviceName = "REVO_DUAL_AXIS_TABLE";
    private const double MaxDegreesPerCommand = 30.0;
    private const double MaxSafeTiltDegrees = 10.0;
    private const double MinRotationPeriodSeconds = 18.0;
    private const double MaxRotationPeriodSeconds = 90.0;
    private const double TiltSetpointToleranceDegrees = 1.0;

    private sealed class Connection : IDisposable
    {
        public BluetoothLEDevice Device;
        public GattDeviceService Service;
        public GattCharacteristic Characteristic;
        public ConcurrentQueue<string> Responses = new ConcurrentQueue<string>();
        public TypedEventHandler<GattCharacteristic, GattValueChangedEventArgs> Handler;
        public bool NotificationsEnabled;

        public void Dispose()
        {
            if (Characteristic != null && Handler != null)
            {
                Characteristic.ValueChanged -= Handler;
            }
            if (NotificationsEnabled && Characteristic != null)
            {
                Characteristic.WriteClientCharacteristicConfigurationDescriptorAsync(
                    GattClientCharacteristicConfigurationDescriptorValue.None).AsTask().GetAwaiter().GetResult();
            }
            if (Service != null) Service.Dispose();
            if (Device != null) Device.Dispose();
        }
    }

    private static int Main(string[] args)
    {
        try
        {
            if (args.Length == 0 || args[0] == "--help" || args[0] == "-h")
            {
                Usage();
                return args.Length == 0 ? 2 : 0;
            }

            string operation = args[0].ToLowerInvariant();
            if (operation == "devices")
            {
                if (args.Length != 2) throw new ArgumentException("devices requires advertisement UUIDs from the device catalog.");
                var advertisedUuids = args[1].Split(',').Select(value => Guid.Parse(value)).Distinct().ToArray();
                Console.OutputEncoding = new UTF8Encoding(false);
                return ListDevicesAsync(advertisedUuids).GetAwaiter().GetResult();
            }
            double degrees = 0;
            double rotationPeriod = 0;
            int monitorSeconds = 0;
            int sweepSteps = 0;
            string monitorPath = null;
            string turntableAddress = null;
            string turntableName = DefaultDeviceName;
            string advertisementUuid = CharacteristicUuid.ToString();
            string serviceUuid = ServiceUuid.ToString();
            string characteristicUuid = CharacteristicUuid.ToString();
            int rotateAfterSeconds = -1;
            ScanCaptureOptions scanCapture = null;
            if (operation == "rotate")
            {
                if (args.Length != 2 || !double.TryParse(args[1], NumberStyles.Float,
                        CultureInfo.InvariantCulture, out degrees) || !IsFinite(degrees) ||
                    Math.Abs(degrees) < 0.1 || Math.Abs(degrees) > MaxDegreesPerCommand)
                {
                    throw new ArgumentException("rotate requires a number from -30 to 30 degrees, excluding zero.");
                }
            }
            else if (operation == "speed")
            {
                if ((args.Length != 2 && args.Length != 7) || !double.TryParse(args[1], NumberStyles.Float,
                        CultureInfo.InvariantCulture, out rotationPeriod) || !IsFinite(rotationPeriod) ||
                    rotationPeriod < MinRotationPeriodSeconds || rotationPeriod > MaxRotationPeriodSeconds)
                {
                    throw new ArgumentException("speed requires seconds per rotation from 18 to 90.");
                }
                if (args.Length == 7)
                {
                    turntableAddress = args[2]; turntableName = args[3];
                    advertisementUuid = args[4]; serviceUuid = args[5]; characteristicUuid = args[6];
                }
            }
            else if (operation == "tilt")
            {
                if (args.Length != 2 || !double.TryParse(args[1], NumberStyles.Float,
                    CultureInfo.InvariantCulture, out degrees) || !IsFinite(degrees) || Math.Abs(degrees) > MaxSafeTiltDegrees)
                    throw new ArgumentException("tilt requires a target angle within the safe range of -10 to 10 degrees.");
            }
            else if (operation == "monitor")
            {
                if ((args.Length != 3 && args.Length != 5) || !Int32.TryParse(args[1], out monitorSeconds) || monitorSeconds < 1 || monitorSeconds > 300)
                    throw new ArgumentException("monitor requires duration (1–300 s), CSV path, and optional rotate-delay plus degrees.");
                monitorPath = args[2];
                if (args.Length == 5 && (!Int32.TryParse(args[3], out rotateAfterSeconds) || rotateAfterSeconds < 0 || rotateAfterSeconds >= monitorSeconds ||
                    !double.TryParse(args[4], NumberStyles.Float, CultureInfo.InvariantCulture, out degrees) || !IsFinite(degrees) ||
                    Math.Abs(degrees) < 0.1 || Math.Abs(degrees) > MaxDegreesPerCommand))
                    throw new ArgumentException("monitor rotation requires delay < duration and degrees from -30 to 30.");
            }
            else if (operation == "sweep")
            {
                if (args.Length != 4 || !Int32.TryParse(args[1], out sweepSteps) || sweepSteps < 1 || sweepSteps > 360 ||
                    !double.TryParse(args[2], NumberStyles.Float, CultureInfo.InvariantCulture, out degrees) ||
                    !IsFinite(degrees) || Math.Abs(degrees) < 0.1 || Math.Abs(degrees) > MaxDegreesPerCommand)
                    throw new ArgumentException("sweep requires 1–360 steps, each from -30 to 30 degrees, and a CSV output path.");
                if (Math.Abs(degrees * sweepSteps) > 360.0)
                    throw new ArgumentException("sweep cannot command more than 360 degrees of total rotation.");
                monitorPath = args[3];
            }
            else if (operation == "capture-sweep")
            {
                double expectedTilt, depthScale;
                if (args.Length != 16 || !Int32.TryParse(args[1], out sweepSteps) || sweepSteps < 1 || sweepSteps > 360 ||
                    !double.TryParse(args[2], NumberStyles.Float, CultureInfo.InvariantCulture, out degrees) ||
                    !IsFinite(degrees) || Math.Abs(degrees) < 0.1 || Math.Abs(degrees) > MaxDegreesPerCommand ||
                    Math.Abs(Math.Abs(degrees * sweepSteps) - 360.0) > 0.01 ||
                    !double.TryParse(args[3], NumberStyles.Float, CultureInfo.InvariantCulture, out expectedTilt) ||
                    !IsFinite(expectedTilt) || Math.Abs(expectedTilt) > MaxSafeTiltDegrees ||
                    !double.TryParse(args[8], NumberStyles.Float, CultureInfo.InvariantCulture, out depthScale) ||
                    !IsFinite(depthScale) || depthScale <= 0.0)
                    throw new ArgumentException("capture-sweep requires one full 360-degree turn using 1–360 steps of at most 30 degrees, an expected tilt within ±10 degrees, CSV, capture executable, output prefix, Q matrix, positive depth scale, and live-frame path.");
                monitorPath = args[4];
                scanCapture = new ScanCaptureOptions {
                    ExpectedTilt = expectedTilt,
                    CaptureExecutable = args[5],
                    OutputPrefix = args[6],
                    CalibrationFile = args[7],
                    DepthScale = depthScale,
                    LiveRawFile = args[9],
                    ScannerDeviceId = args[10],
                    TurntableAddress = args[11],
                    TurntableName = args[12],
                    AdvertisementUuid = args[13],
                    ServiceUuid = args[14],
                    CharacteristicUuid = args[15],
                };
                if (!File.Exists(scanCapture.CaptureExecutable) || !File.Exists(scanCapture.CalibrationFile))
                    throw new ArgumentException("CaptureY16.exe and Q.bin must exist before capture-sweep starts.");
            }
            else if ((operation == "query" || operation == "zero" || operation == "stop") && args.Length != 1)
            {
                throw new ArgumentException("query, zero and stop do not accept extra arguments.");
            }
            else if (operation != "query" && operation != "zero" && operation != "stop" && operation != "speed" && operation != "tilt" && operation != "monitor" && operation != "sweep" && operation != "capture-sweep")
            {
                throw new ArgumentException("Operation must be query, zero, stop, speed, tilt, monitor, sweep, capture-sweep, or rotate.");
            }

            return RunAsync(operation, degrees, rotationPeriod, monitorSeconds, monitorPath, rotateAfterSeconds,
                sweepSteps, scanCapture, turntableAddress, turntableName,
                advertisementUuid, serviceUuid, characteristicUuid).GetAwaiter().GetResult();
        }
        catch (Exception error)
        {
            Console.Error.WriteLine("Turntable command failed: " + error.Message);
            return 1;
        }
    }

    private static void Usage()
    {
        Console.WriteLine("TurntableController.exe query");
        Console.WriteLine("TurntableController.exe devices <advertisement-uuid>");
        Console.WriteLine("TurntableController.exe zero");
        Console.WriteLine("TurntableController.exe speed <seconds-per-rotation> [address name advertisement-uuid service-uuid characteristic-uuid]   (18–90 seconds)");
        Console.WriteLine("TurntableController.exe tilt <degrees>   (safe operating limit: -10–10 degrees)");
        Console.WriteLine("TurntableController.exe rotate <degrees>   (one bounded relative move, max 30 degrees)");
        Console.WriteLine("TurntableController.exe stop");
        Console.WriteLine("TurntableController.exe monitor <seconds> <output.csv>");
        Console.WriteLine("TurntableController.exe monitor <seconds> <output.csv> <rotate-delay-seconds> <degrees>");
        Console.WriteLine("TurntableController.exe sweep <steps> <degrees-per-step> <output.csv>   (one BLE connection; each step is verified before the next)");
        Console.WriteLine("TurntableController.exe capture-sweep <steps> <degrees-per-step> <tilt> <poses.csv> <CaptureY16.exe> <output-prefix> <Q.bin> <depth-scale> <live.raw> <scanner-id> <table-address> <table-name> <advertisement-uuid> <service-uuid> <characteristic-uuid>");
        Console.WriteLine("The app does not pair the turntable. It discovers the BLE device by its advertised FFE1 service,\n" +
                          "then confirms the GATT device name before sending any command.");
    }

    private static void EnsureScannerDisconnected()
    {
        string[] scannerProcesses = { "CaptureY16", "CaptureRgb", "RevoScan", "RevoScan5", "RevoAssistant" };
        Process[] running = Process.GetProcesses();
        string[] active;
        try
        {
            active = running.Where(process => scannerProcesses.Contains(process.ProcessName, StringComparer.OrdinalIgnoreCase))
                .Select(process => process.ProcessName).Distinct(StringComparer.OrdinalIgnoreCase).ToArray();
        }
        finally
        {
            foreach (Process process in running) process.Dispose();
        }
        if (active.Length > 0)
            throw new InvalidOperationException("Turntable motion is blocked while scanner processes are active: " + String.Join(", ", active) + ". Stop and disconnect the scanner before moving the table.");
    }

    private static async Task<int> RunAsync(string operation, double degrees, double rotationPeriod,
                                            int monitorSeconds, string monitorPath, int rotateAfterSeconds,
                                            int sweepSteps, ScanCaptureOptions scanCapture,
                                            string turntableAddress, string turntableName,
                                            string advertisementUuid, string serviceUuid, string characteristicUuid)
    {
        bool motionRequested = operation == "rotate" || operation == "sweep" || operation == "capture-sweep" ||
            operation == "tilt" || operation == "zero" || (operation == "monitor" && rotateAfterSeconds >= 0);
        if (motionRequested) EnsureScannerDisconnected();
        using (Connection connection = await ConnectAsync(
            scanCapture != null ? scanCapture.TurntableAddress : turntableAddress,
            scanCapture != null ? scanCapture.TurntableName : turntableName,
            scanCapture != null ? scanCapture.AdvertisementUuid : advertisementUuid,
            scanCapture != null ? scanCapture.ServiceUuid : serviceUuid,
            scanCapture != null ? scanCapture.CharacteristicUuid : characteristicUuid))
        {
            await EnableNotificationsAsync(connection);
            if (operation == "query")
            {
                foreach (string command in new[] { "+QT,CHANGEANGLE;", "+QR,TILTVALUE;", "+QT,TURNSPEED;", "+QR,TILTSPEED;" })
                {
                    Console.WriteLine(command + " => " + await SendAndReadAsync(connection, command));
                }
                return 0;
            }

            if (operation == "monitor")
            {
                string fullPath = System.IO.Path.GetFullPath(monitorPath);
                using (var writer = new System.IO.StreamWriter(fullPath, false, new UTF8Encoding(false)))
                {
                    writer.WriteLine("utc,angle_degrees,tilt_degrees,rotation_response,tilt_response");
                    var monitorDeadline = DateTime.UtcNow.AddSeconds(monitorSeconds);
                    var monitorTimer = Stopwatch.StartNew();
                    int sample = 0;
                    bool moveSent = false;
                    while (DateTime.UtcNow < monitorDeadline)
                    {
                        if (!moveSent && rotateAfterSeconds >= 0 && monitorTimer.Elapsed.TotalSeconds >= rotateAfterSeconds)
                        {
                            EnsureScannerDisconnected();
                            string monitorMove = "+CT,TURNANGLE=" + degrees.ToString("F1", CultureInfo.InvariantCulture) + ";";
                            Console.WriteLine("issuing " + monitorMove);
                            string reply = await SendAndReadAsync(connection, monitorMove);
                            writer.WriteLine("# " + DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture) + " command " + monitorMove + " => " + reply);
                            writer.Flush();
                            moveSent = true;
                        }
                        string response = await SendAndReadAsync(connection, "+QT,CHANGEANGLE;");
                        string tiltResponse = await SendAndReadAsync(connection, "+QR,TILTVALUE;");
                        double angle, tilt;
                        DateTime observedAt = DateTime.UtcNow;
                        if (TryAngle(response, out angle) && TryAngle(tiltResponse, out tilt))
                        {
                            writer.WriteLine(observedAt.ToString("o", CultureInfo.InvariantCulture) + "," +
                                angle.ToString("F2", CultureInfo.InvariantCulture) + "," +
                                tilt.ToString("F2", CultureInfo.InvariantCulture) + ",\"" + response.Replace("\"", "\"\"") + "\",\"" +
                                tiltResponse.Replace("\"", "\"\"") + "\"");
                            writer.Flush();
                            if (sample++ % 5 == 0) Console.WriteLine(observedAt.ToString("o") + " angle=" + angle.ToString("F1", CultureInfo.InvariantCulture));
                        }
                        await Task.Delay(50);
                    }
                }
                return 0;
            }

            if (operation == "sweep" || operation == "capture-sweep")
            {
                string fullPath = System.IO.Path.GetFullPath(monitorPath);
                if (System.IO.File.Exists(fullPath))
                    throw new InvalidOperationException("Sweep output already exists; refusing to overwrite: " + fullPath);
                string directory = System.IO.Path.GetDirectoryName(fullPath);
                if (!String.IsNullOrEmpty(directory)) System.IO.Directory.CreateDirectory(directory);
                using (var writer = new System.IO.StreamWriter(fullPath, false, new UTF8Encoding(false)))
                {
                    writer.WriteLine("utc,step_index,event,target_delta_degrees,angle_degrees,tilt_degrees,command,rotation_response,tilt_response");
                    string initialRotation = await SendAndReadAsync(connection, "+QT,CHANGEANGLE;");
                    string initialTilt = await SendAndReadAsync(connection, "+QR,TILTVALUE;");
                    double currentAngle, currentTilt;
                    if (!TryAngle(initialRotation, out currentAngle) || !TryAngle(initialTilt, out currentTilt))
                        throw new InvalidOperationException("Could not read both table axes; no sweep command was sent.");
                    double baselineTilt = currentTilt;
                    double baselineAngle = currentAngle;
                    if (operation == "sweep" && Math.Abs(baselineTilt) > 0.5)
                        throw new InvalidOperationException("Rotation-only sweep requires zero tilt.");
                    if (operation == "capture-sweep" && Math.Abs(baselineTilt - scanCapture.ExpectedTilt) > TiltSetpointToleranceDegrees)
                        throw new InvalidOperationException("Turntable tilt does not match the requested scan-pass tilt; no rotation command was sent.");
                    WriteSweepRow(writer, 0, "baseline", 0, currentAngle, currentTilt, "", initialRotation, initialTilt);
                    Console.WriteLine("sweep baseline: angle=" + currentAngle.ToString("F1", CultureInfo.InvariantCulture) +
                                      " deg, tilt=" + currentTilt.ToString("F1", CultureInfo.InvariantCulture) + " deg");
                    if (operation == "capture-sweep")
                    {
                        string prefix = scanCapture.OutputPrefix + "_pose_000";
                        WriteSweepRow(writer, 0, "capture_started", 0, currentAngle, currentTilt, prefix, initialRotation, initialTilt);
                        CaptureY16Pose(scanCapture, prefix);
                        WriteSweepRow(writer, 0, "capture_completed", 0, currentAngle, currentTilt, prefix, initialRotation, initialTilt);
                        await PauseAfterCaptureAsync();
                        Console.WriteLine("captured pose 0/11: angle=" + currentAngle.ToString("F1", CultureInfo.InvariantCulture) + " deg, tilt=" + currentTilt.ToString("F1", CultureInfo.InvariantCulture));
                    }

                    for (int step = 1; step <= sweepSteps; ++step)
                    {
                        EnsureScannerDisconnected();
                        double sweepBefore = currentAngle;
                        string command = "+CT,TURNANGLE=" + degrees.ToString("F1", CultureInfo.InvariantCulture) + ";";
                        string commandReply = await SendAndReadAsync(connection, command);
                        WriteSweepRow(writer, step, "command_sent", degrees, sweepBefore, currentTilt, command, commandReply, "");
                        Console.WriteLine("sweep step " + step + "/" + sweepSteps + ": " + command + " => " + commandReply);

                        bool reached = false;
                        DateTime sweepDeadline = DateTime.UtcNow.AddSeconds(10.0 + Math.Abs(degrees) * 90.0 / 360.0);
                        while (DateTime.UtcNow < sweepDeadline)
                        {
                            string rotationResponse = await SendAndReadAsync(connection, "+QT,CHANGEANGLE;");
                            string tiltResponse = await SendAndReadAsync(connection, "+QR,TILTVALUE;");
                            double angle, tilt;
                            if (TryAngle(rotationResponse, out angle) && TryAngle(tiltResponse, out tilt))
                            {
                                currentAngle = angle;
                                currentTilt = tilt;
                                double sweepMeasured = SignedAngleDelta(sweepBefore, currentAngle);
                                bool targetReached = Math.Abs(sweepMeasured - degrees) <= 1.0;
                                WriteSweepRow(writer, step, targetReached ? "target_reached" : "telemetry",
                                    degrees, currentAngle, currentTilt, "", rotationResponse, tiltResponse);
                                if (Math.Abs(currentTilt - baselineTilt) > 0.5)
                                {
                                    await SendAndReadAsync(connection, "+CT,STOP;");
                                    throw new InvalidOperationException("Turntable tilt changed during rotation; rotation was stopped.");
                                }
                                if (targetReached)
                                {
                                    reached = true;
                                    Console.WriteLine("step " + step + " reached: angle=" + currentAngle.ToString("F1", CultureInfo.InvariantCulture) +
                                                      " deg, measured delta=" + sweepMeasured.ToString("F1", CultureInfo.InvariantCulture) + " deg");
                                    break;
                                }
                            }
                            else
                            {
                                WriteSweepRow(writer, step, "unreadable_telemetry", degrees, Double.NaN, Double.NaN,
                                    "", rotationResponse, tiltResponse);
                            }
                        }
                        if (!reached)
                        {
                            await SendAndReadAsync(connection, "+CT,STOP;");
                            throw new InvalidOperationException("Sweep step " + step + " did not reach its target; no further move was issued.");
                        }
                        if (operation == "capture-sweep" && step < sweepSteps)
                        {
                            string prefix = scanCapture.OutputPrefix + "_pose_" + step.ToString("D3", CultureInfo.InvariantCulture);
                            WriteSweepRow(writer, step, "capture_started", degrees, currentAngle, currentTilt, prefix, "", "");
                            CaptureY16Pose(scanCapture, prefix);
                            WriteSweepRow(writer, step, "capture_completed", degrees, currentAngle, currentTilt, prefix, "", "");
                            await PauseAfterCaptureAsync();
                            Console.WriteLine("captured pose " + step + "/11: angle=" + currentAngle.ToString("F1", CultureInfo.InvariantCulture) + " deg, tilt=" + currentTilt.ToString("F1", CultureInfo.InvariantCulture));
                        }
                    }
                    if (operation == "capture-sweep")
                    {
                        double finalAngle = await ReadAngleAsync(connection);
                        if (Math.Abs(SignedAngleDelta(baselineAngle, finalAngle)) > 1.0)
                            throw new InvalidOperationException("The final full-turn angle did not return to its starting position.");
                    }
                    Console.WriteLine("sweep completed: " + sweepSteps + " steps, commanded total=" +
                                      (sweepSteps * degrees).ToString("F1", CultureInfo.InvariantCulture) + " deg");
                }
                return 0;
            }

            if (operation == "stop")
            {
                Console.WriteLine("rotation stop: " + await SendAndReadAsync(connection, "+CT,STOP;"));
                Console.WriteLine("tilt stop: " + await SendAndReadAsync(connection, "+CR,STOP;"));
                return 0;
            }

            if (operation == "speed")
            {
                string command = "+CT,TURNSPEED=" + rotationPeriod.ToString("F1", CultureInfo.InvariantCulture) + ";";
                Console.WriteLine(command + " => " + await SendAndReadAsync(connection, command));
                Console.WriteLine("Requested rotation period: " + rotationPeriod.ToString("F1", CultureInfo.InvariantCulture) + " seconds per revolution.");
                return 0;
            }

            if (operation == "tilt")
            {
                EnsureScannerDisconnected();
                string tiltBeforeText = await SendAndReadAsync(connection, "+QR,TILTVALUE;");
                double tiltBefore;
                if (!TryAngle(tiltBeforeText, out tiltBefore))
                    throw new InvalidOperationException("Could not read current tilt angle; no tilt command was sent.");
                Console.WriteLine("tilt before: " + tiltBefore.ToString("F1", CultureInfo.InvariantCulture) + " deg");
                EnsureScannerDisconnected();
                string command = "+CR,TILTVALUE=" + degrees.ToString("F1", CultureInfo.InvariantCulture) + ";";
                Console.WriteLine(command + " => " + await SendAndReadAsync(connection, command));
                DateTime tiltDeadline = DateTime.UtcNow.AddSeconds(30);
                double tiltAfter = Double.NaN;
                while (DateTime.UtcNow < tiltDeadline)
                {
                    await Task.Delay(300);
                    string response = await SendAndReadAsync(connection, "+QR,TILTVALUE;");
                    if (TryAngle(response, out tiltAfter) && Math.Abs(tiltAfter - degrees) <= TiltSetpointToleranceDegrees)
                    {
                        Console.WriteLine("tilt after: " + tiltAfter.ToString("F1", CultureInfo.InvariantCulture) + " deg");
                        return 0;
                    }
                }
                Console.Error.WriteLine("Tilt did not reach the requested angle within 30 s.");
                return 1;
            }

            if (operation == "zero")
            {
                EnsureScannerDisconnected();
                string zeroBaselineText = await SendAndReadAsync(connection, "+QT,CHANGEANGLE;");
                double zeroBaseline;
                if (!TryAngle(zeroBaselineText, out zeroBaseline))
                {
                    throw new InvalidOperationException("Could not read the current angle; zero command was not sent.");
                }
                string tiltBaselineText = await SendAndReadAsync(connection, "+QR,TILTVALUE;");
                double tiltBaseline;
                if (!TryAngle(tiltBaselineText, out tiltBaseline))
                {
                    throw new InvalidOperationException("Could not read the current tilt; zero commands were not sent.");
                }
                Console.WriteLine("angle before zero: " + zeroBaseline.ToString("F1", CultureInfo.InvariantCulture) + " deg");
                Console.WriteLine("tilt before zero: " + tiltBaseline.ToString("F1", CultureInfo.InvariantCulture) + " deg");
                EnsureScannerDisconnected();
                string reply = await SendAndReadAsync(connection, "+CT,TOZERO;");
                Console.WriteLine("+CT,TOZERO; => " + reply);
                EnsureScannerDisconnected();
                string tiltReply = await SendAndReadAsync(connection, "+CR,TOZERO;");
                Console.WriteLine("+CR,TOZERO; => " + tiltReply);
                for (int attempt = 0; attempt < 30; ++attempt)
                {
                    await Task.Delay(1000);
                    string currentText = await SendAndReadAsync(connection, "+QT,CHANGEANGLE;");
                    string currentTiltText = await SendAndReadAsync(connection, "+QR,TILTVALUE;");
                    double current, currentTilt;
                    if (TryAngle(currentText, out current) && TryAngle(currentTiltText, out currentTilt))
                    {
                        double normalized = SignedAngleDelta(0, current);
                        if (Math.Abs(normalized) <= 0.5 && Math.Abs(currentTilt) <= 0.5)
                        {
                            Console.WriteLine("angle after zero: " + normalized.ToString("F1", CultureInfo.InvariantCulture) + " deg");
                            Console.WriteLine("tilt after zero: " + currentTilt.ToString("F1", CultureInfo.InvariantCulture) + " deg");
                            return 0;
                        }
                    }
                }
                Console.Error.WriteLine("Rotation or tilt did not report zero within 30 checks; no automatic retry was sent.");
                return 1;
            }

            string beforeText = await SendAndReadAsync(connection, "+QT,CHANGEANGLE;");
            double before;
            if (!TryAngle(beforeText, out before))
            {
                throw new InvalidOperationException("Could not read the current angle; no movement was sent.");
            }
            Console.WriteLine("angle before: " + before.ToString("F1", CultureInfo.InvariantCulture) + " deg");

            EnsureScannerDisconnected();
            string move = "+CT,TURNANGLE=" + degrees.ToString("F1", CultureInfo.InvariantCulture) + ";";
            var timer = Stopwatch.StartNew();
            string moveReply = await SendAndReadAsync(connection, move);
            Console.WriteLine(move + " => " + moveReply);
            double after = Double.NaN;
            double measured = Double.NaN;
            bool reachedTarget = false;
            DateTime deadline = DateTime.UtcNow.AddSeconds(10.0 + Math.Abs(degrees) * 90.0 / 360.0);
            while (DateTime.UtcNow < deadline)
            {
                await Task.Delay(200);
                string afterText = await SendAndReadAsync(connection, "+QT,CHANGEANGLE;");
                if (!TryAngle(afterText, out after)) continue;
                measured = SignedAngleDelta(before, after);
                if (Math.Abs(measured - degrees) <= 1.0)
                {
                    reachedTarget = true;
                    timer.Stop();
                    break;
                }
            }
            if (Double.IsNaN(after))
            {
                Console.WriteLine("angle after: unavailable; no further motor command was sent");
                return 1;
            }
            Console.WriteLine("angle after: " + after.ToString("F1", CultureInfo.InvariantCulture) + " deg");
            Console.WriteLine("measured change: " + measured.ToString("F1", CultureInfo.InvariantCulture) + " deg");
            if (reachedTarget)
            {
                Console.WriteLine("movement completion time: " + timer.Elapsed.TotalSeconds.ToString("F2", CultureInfo.InvariantCulture) + " s");
            }
            if (!reachedTarget)
            {
                Console.Error.WriteLine("Rotation did not reach the target before timeout; do not retry automatically.");
                return 1;
            }
            return 0;
        }
    }

    private static async Task<int> ListDevicesAsync(Guid[] advertisedUuids)
    {
        Console.WriteLine("Searching nearby BLE devices advertising the supported service...");
        foreach (ulong address in await FindAdvertisedAddressesAsync(advertisedUuids))
        {
            using (BluetoothLEDevice device = await BluetoothLEDevice.FromBluetoothAddressAsync(address))
            {
                if (device != null)
                    Console.WriteLine(address.ToString("X12", CultureInfo.InvariantCulture) + "\t" + device.Name);
            }
        }
        return 0;
    }

    private static async Task<ulong[]> FindAdvertisedAddressesAsync(Guid advertisedUuid)
    {
        return await FindAdvertisedAddressesAsync(new[] { advertisedUuid });
    }

    private static async Task<ulong[]> FindAdvertisedAddressesAsync(Guid[] advertisedUuids)
    {
        var addresses = new ConcurrentDictionary<ulong, byte>();
        var watcher = new BluetoothLEAdvertisementWatcher { ScanningMode = BluetoothLEScanningMode.Active };
        TypedEventHandler<BluetoothLEAdvertisementWatcher, BluetoothLEAdvertisementReceivedEventArgs> handler = (sender, eventArgs) =>
        {
            if (eventArgs.Advertisement.ServiceUuids.Any(advertisedUuids.Contains))
                addresses[eventArgs.BluetoothAddress] = 0;
        };
        watcher.Received += handler;
        try
        {
            watcher.Start();
            await Task.Delay(TimeSpan.FromSeconds(8));
        }
        finally
        {
            try { watcher.Stop(); } catch { }
            watcher.Received -= handler;
        }
        return addresses.Keys.ToArray();
    }

    private static async Task<Connection> ConnectAsync(string selectedAddress, string expectedName,
                                                        string advertisedUuidText, string serviceUuidText,
                                                        string characteristicUuidText)
    {
        Console.WriteLine("Searching nearby BLE devices for the Revopoint turntable...");
        ulong requestedAddress = 0;
        Guid advertisedUuid, serviceUuid, characteristicUuid;
        if (!Guid.TryParse(advertisedUuidText, out advertisedUuid) ||
            !Guid.TryParse(serviceUuidText, out serviceUuid) ||
            !Guid.TryParse(characteristicUuidText, out characteristicUuid))
            throw new ArgumentException("The selected turntable GATT profile has invalid UUIDs.");
        if (!String.IsNullOrWhiteSpace(selectedAddress) &&
            !UInt64.TryParse(selectedAddress, NumberStyles.HexNumber, CultureInfo.InvariantCulture, out requestedAddress))
            throw new ArgumentException("The selected turntable address is invalid.");
        foreach (ulong address in await FindAdvertisedAddressesAsync(advertisedUuid))
        {
            if (requestedAddress != 0 && address != requestedAddress) continue;
            BluetoothLEDevice device = null;
            try
            {
                device = await BluetoothLEDevice.FromBluetoothAddressAsync(address);
                if (device == null || !String.Equals(device.Name, expectedName, StringComparison.Ordinal))
                {
                    if (device != null) device.Dispose();
                    continue;
                }

                var serviceResult = await device.GetGattServicesForUuidAsync(serviceUuid, BluetoothCacheMode.Uncached);
                if (serviceResult.Status != GattCommunicationStatus.Success || serviceResult.Services.Count != 1)
                {
                    foreach (var unmatchedService in serviceResult.Services) unmatchedService.Dispose();
                    device.Dispose();
                    continue;
                }

                GattDeviceService service = serviceResult.Services[0];
                var characteristicResult = await service.GetCharacteristicsForUuidAsync(characteristicUuid, BluetoothCacheMode.Uncached);
                if (characteristicResult.Status != GattCommunicationStatus.Success || characteristicResult.Characteristics.Count != 1)
                {
                    service.Dispose();
                    device.Dispose();
                    continue;
                }

                GattCharacteristic characteristic = characteristicResult.Characteristics[0];
                var properties = characteristic.CharacteristicProperties;
                if ((properties & GattCharacteristicProperties.Notify) == 0 ||
                    ((properties & GattCharacteristicProperties.WriteWithoutResponse) == 0 &&
                     (properties & GattCharacteristicProperties.Write) == 0))
                {
                    service.Dispose();
                    device.Dispose();
                    continue;
                }
                Console.WriteLine("Connected to " + expectedName + " at " + address.ToString("X12", CultureInfo.InvariantCulture) + " over BLE (no pairing performed).");
                return new Connection { Device = device, Service = service, Characteristic = characteristic };
            }
            catch
            {
                if (device != null) device.Dispose();
            }
        }
        throw new InvalidOperationException("The selected turntable is unavailable or does not expose the expected GATT service.");
    }

    private static async Task EnableNotificationsAsync(Connection connection)
    {
        connection.Handler = (sender, eventArgs) => connection.Responses.Enqueue(ReadAscii(eventArgs.CharacteristicValue));
        connection.Characteristic.ValueChanged += connection.Handler;
        var status = await connection.Characteristic.WriteClientCharacteristicConfigurationDescriptorAsync(
            GattClientCharacteristicConfigurationDescriptorValue.Notify);
        if (status != GattCommunicationStatus.Success)
        {
            throw new InvalidOperationException("Could not enable turntable status notifications: " + status);
        }
        connection.NotificationsEnabled = true;
    }

    private static async Task<string> SendAndReadAsync(Connection connection, string command)
    {
        string ignored;
        while (connection.Responses.TryDequeue(out ignored)) { }

        var writer = new DataWriter();
        writer.WriteBytes(Encoding.ASCII.GetBytes(command + "\r\n"));
        GattWriteOption option =
            (connection.Characteristic.CharacteristicProperties & GattCharacteristicProperties.WriteWithoutResponse) != 0
                ? GattWriteOption.WriteWithoutResponse
                : GattWriteOption.WriteWithResponse;
        var writeStatus = await connection.Characteristic.WriteValueAsync(writer.DetachBuffer(), option);
        if (writeStatus != GattCommunicationStatus.Success)
        {
            throw new InvalidOperationException("BLE write failed for " + command + ": " + writeStatus);
        }
        await Task.Delay(200);

        string response;
        string last = null;
        while (connection.Responses.TryDequeue(out response)) last = response;
        if (last != null) return last;

        var read = await connection.Characteristic.ReadValueAsync(BluetoothCacheMode.Uncached);
        if (read.Status != GattCommunicationStatus.Success)
        {
            throw new InvalidOperationException("No BLE notification and readback failed for " + command + ".");
        }
        return ReadAscii(read.Value);
    }

    private static string ReadAscii(IBuffer buffer)
    {
        if (buffer == null || buffer.Length == 0) return "<empty>";
        var reader = DataReader.FromBuffer(buffer);
        var bytes = new byte[reader.UnconsumedBufferLength];
        reader.ReadBytes(bytes);
        return Encoding.ASCII.GetString(bytes).Replace("\r", "").Replace("\n", "");
    }

    private static void WriteSweepRow(System.IO.StreamWriter writer, int step, string eventName,
                                      double targetDelta, double angle, double tilt, string command,
                                      string rotationResponse, string tiltResponse)
    {
        writer.WriteLine(String.Join(",", new[] {
            DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture),
            step.ToString(CultureInfo.InvariantCulture),
            Csv(eventName),
            targetDelta.ToString("F2", CultureInfo.InvariantCulture),
            IsFinite(angle) ? angle.ToString("F2", CultureInfo.InvariantCulture) : "",
            IsFinite(tilt) ? tilt.ToString("F2", CultureInfo.InvariantCulture) : "",
            Csv(command), Csv(rotationResponse), Csv(tiltResponse),
        }));
        writer.Flush();
    }

    private sealed class ScanCaptureOptions
    {
        public double ExpectedTilt;
        public string CaptureExecutable;
        public string OutputPrefix;
        public string CalibrationFile;
        public double DepthScale;
        public string LiveRawFile;
        public string ScannerDeviceId;
        public string TurntableAddress;
        public string TurntableName;
        public string AdvertisementUuid;
        public string ServiceUuid;
        public string CharacteristicUuid;
    }

    private static async Task PauseAfterCaptureAsync()
    {
        Console.WriteLine("scanner processing pause after frame: 3 seconds");
        await Task.Delay(TimeSpan.FromSeconds(3));
    }

    private static void CaptureY16Pose(ScanCaptureOptions options, string prefix)
    {
        var start = new ProcessStartInfo {
            FileName = options.CaptureExecutable,
            Arguments = String.Join(" ", new[] {
                "--out-prefix", prefix,
                "--q-file", options.CalibrationFile,
                "--depth-scale", options.DepthScale.ToString("R", CultureInfo.InvariantCulture),
                "--frames", "1",
                "--live-raw", options.LiveRawFile,
                "--device-id", options.ScannerDeviceId,
            }.Select(QuoteWindowsArgument)),
            UseShellExecute = false,
            CreateNoWindow = true,
        };
        using (Process process = Process.Start(start))
        {
            if (!process.WaitForExit(30000))
            {
                process.Kill();
                throw new TimeoutException("CaptureY16 exceeded 30 seconds at " + prefix + ".");
            }
            if (process.ExitCode != 0)
                throw new InvalidOperationException("CaptureY16 failed at " + prefix + " with exit code " + process.ExitCode + ".");
        }
    }

    private static string QuoteWindowsArgument(string value)
    {
        if (String.IsNullOrEmpty(value)) return "\"\"";
        if (value.IndexOfAny(new[] { ' ', '\t', '\n', '\v', '\"' }) < 0) return value;
        var result = new StringBuilder("\"");
        int slashes = 0;
        foreach (char character in value)
        {
            if (character == '\\') { slashes++; continue; }
            if (character == '\"')
            {
                result.Append('\\', slashes * 2 + 1).Append('\"');
                slashes = 0;
                continue;
            }
            result.Append('\\', slashes).Append(character);
            slashes = 0;
        }
        result.Append('\\', slashes * 2).Append('\"');
        return result.ToString();
    }

    private static async Task<double> ReadAngleAsync(Connection connection)
    {
        string response = await SendAndReadAsync(connection, "+QT,CHANGEANGLE;");
        double angle;
        if (!TryAngle(response, out angle)) throw new InvalidOperationException("Could not read the final turntable angle.");
        return angle;
    }

    private static string Csv(string value)
    {
        return "\"" + (value ?? "").Replace("\"", "\"\"") + "\"";
    }

    private static bool TryAngle(string response, out double angle)
    {
        Match match = Regex.Match(response ?? String.Empty, @"\+DATA=\s*(-?\d+(?:\.\d+)?)");
        return double.TryParse(match.Groups[1].Value, NumberStyles.Float, CultureInfo.InvariantCulture, out angle);
    }

    private static double SignedAngleDelta(double before, double after)
    {
        double delta = after - before;
        if (delta > 180.0) delta -= 360.0;
        if (delta < -180.0) delta += 360.0;
        return delta;
    }

    private static bool IsFinite(double value)
    {
        return !Double.IsNaN(value) && !Double.IsInfinity(value);
    }
}
