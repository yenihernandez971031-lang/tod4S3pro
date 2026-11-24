plugins {
    id("com.android.application")
}

dependencies {
    implementation("androidx.activity:activity:1.10.1")
    implementation("androidx.work:work-runtime:2.10.1")
}

android {
    namespace = "com.tod4s3.sync"
    compileSdk = 35

    androidResources {
        noCompress += "dat"
    }

    defaultConfig {
        applicationId = "com.tod4s3.sync"
        minSdk = 24
        targetSdk = 35
        versionCode = 135
        versionName = "1.5.90"
    }
}
